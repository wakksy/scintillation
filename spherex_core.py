"""
spherex_core.py -- the "engine" behind the Scintillation web app (app.py).

AI use (NASA Space Apps SI disclosure): this file was written with help from
Claude (Anthropic), used through Claude Code, and reviewed and tested by the
Scintillation team. The idea and design decisions are the team's. See README.md.

It contains no web-page code, only the science and data plumbing:
  * parse_coord      read RA/Dec typed by a person
  * find_images      ask IRSA which SPHEREx images cover a position
  * probe_frames     read every candidate image's wavelength from tiny, fast cutouts
  * download_full    download the full-size cutouts of ONLY the chosen frames, in parallel
  * get_frame        download one cutout (disk cache + retries)
  * choose_frames    pick frames that are far apart in time and similar in wavelength
  * align_frames     resample every frame onto one common north-up sky grid
  * make_gif_bytes   render the aligned frames as an animated GIF
  * measure_target   target brightness on the original pixels (for spectra)
  * spectrum_points  one spectrum point per frame; bin_spectrum: 17 bins per detector
  * find_gaia        Gaia DR3 stars in the field (ESA archive, ARI mirror as backup)
  * nearest_gaia     which Gaia star is the target; gaia_details: its facts

Method adapted from the IRSA tutorial "Download a collection of SPHEREx Spectral
Image cutouts as a multi-extension FITS file":
https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts
"""

import hashlib
import io
import logging
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.utils.data import conf
from astropy.visualization import ZScaleInterval
from astropy.wcs import WCS, FITSFixedWarning
from astroquery.ipac.irsa import Irsa
from PIL import Image, ImageDraw, ImageFont
from reproject import reproject_interp

# Quiet harmless metadata warnings (the IRSA tutorial does the same).
logging.getLogger("astropy").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FITSFixedWarning)

# Time limits, so a slow server can never leave the app waiting forever.
REQUEST_TIMEOUT_S = 60      # one file download may take at most this long
TAP_TIMEOUT_S = 60          # the IRSA search ("which images cover this spot?")
PROBE_DEADLINE_S = 90       # all wavelength probes together
DOWNLOAD_DEADLINE_S = 240   # all full-size downloads together
RETRY_DEADLINE_S = 60       # the one slower retry pass
conf.remote_timeout = REQUEST_TIMEOUT_S

PIXEL_SCALE_ARCSEC = 6.2   # SPHEREx pixel size
CUTOUT_FACTOR = 1.45       # download larger than shown so tilted frames fill the view
CACHE_DIR = "cache"
DETECTORS = ["D1", "D2", "D3", "D4", "D5", "D6"]
DETECTOR_BANDS_UM = {"D1": (0.75, 1.09), "D2": (1.10, 1.62), "D3": (1.63, 2.41),
                     "D4": (2.42, 3.82), "D5": (3.83, 4.41), "D6": (4.42, 5.00)}
CHANNELS_PER_DETECTOR = 17   # 102 SPHEREx bands / 6 detectors; used to bin spectra
STAGE_PX = 960   # pictures are rendered this wide, then shown at the page's width


def _label_size(img):
    """Label text that stays readable on large pictures."""
    return max(14, img.width // 34)


# ---- Coordinates -------------------------------------------------------------
def parse_coord(ra_text, dec_text):
    """Turn typed text into a SkyCoord.

    Accepts degrees ("133.795", "-7.245") or sexagesimal
    ("08h55m10.831s", "-07d14m42.53s" or "08 55 10.831", "-07 14 42.53").
    """
    ra_text, dec_text = str(ra_text).strip(), str(dec_text).strip()
    if not ra_text or not dec_text:
        raise ValueError("Please enter both RA and Dec.")
    try:
        ra, dec = float(ra_text), float(dec_text)
    except ValueError:
        try:
            return SkyCoord(f"{ra_text} {dec_text}", unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as err:
            raise ValueError("Could not read those coordinates. Use degrees "
                             "(133.795, -7.245) or hours/degrees (08h55m10.8s, -07d14m42s).") from err
    if not 0 <= ra < 360:
        raise ValueError("RA in degrees must be between 0 and 360.")
    if not -90 <= dec <= 90:
        raise ValueError("Dec must be between -90 and +90 degrees.")
    return SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")


# ---- Querying ----------------------------------------------------------------
def release_of(url):
    """Pull the release name (e.g. 'qr3' or 'dr1') out of an IRSA file path."""
    parts = str(url).split("/spherex/")
    return parts[1].split("/")[0] if len(parts) > 1 else "unknown"


def call_with_timeout(fn, *args, timeout_s=60):
    """Run fn(*args), but give up waiting after timeout_s seconds (raises TimeoutError).

    The abandoned call keeps running in the background until it ends by itself.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn, *args).result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


TAP_RETRY_WAITS_S = (3, 8)   # waits before the 2nd and 3rd try when IRSA says "busy"
_BUSY_SIGNS = ("502", "503", "504", "Proxy Error", "Service Unavailable", "Gateway")


def is_busy_error(err):
    """True for quick 'temporarily unavailable' answers (worth retrying), not real errors."""
    return any(s in str(err) for s in _BUSY_SIGNS)


def find_images(coord, cutout_deg, detector="D2", timeout_s=None, on_retry=None):
    """Ask IRSA for every SPHEREx image covering this position (oldest first).

    If IRSA answers "busy" (502/503/504), it waits and tries again, up to 3 tries in
    total, but never longer than the overall time limit. A timeout is not retried
    (it already used up the time). on_retry(try_number, error) is called before each
    retry, so the app can tell the user.
    """
    budget = timeout_s or TAP_TIMEOUT_S
    t0 = time.time()
    for attempt in range(len(TAP_RETRY_WAITS_S) + 1):
        try:
            return _find_images_once(coord, cutout_deg, detector,
                                     max(5.0, budget - (time.time() - t0)))
        except Exception as err:
            out_of_tries = attempt >= len(TAP_RETRY_WAITS_S)
            wait = 0 if out_of_tries else TAP_RETRY_WAITS_S[attempt]
            if (out_of_tries or not is_busy_error(err)
                    or time.time() - t0 + wait > budget - 5):
                raise
            if on_retry:
                on_retry(attempt + 2, err)
            time.sleep(wait)


def _find_images_once(coord, cutout_deg, detector, timeout_s):
    """One TAP query. Each row has a cutout URL ("uri") and the observation time
    ("time_bounds_lower", MJD)."""
    if detector not in DETECTORS:
        raise ValueError(f"Unknown detector {detector!r}")
    query = f"""
    SELECT
        'https://irsa.ipac.caltech.edu/' || a.uri
            || '?center={coord.ra.deg},{coord.dec.deg}d&size={cutout_deg}' AS uri,
        p.time_bounds_lower
    FROM spherex.artifact a
    JOIN spherex.plane p ON a.planeid = p.planeid
    WHERE 1 = CONTAINS(POINT('ICRS', {coord.ra.deg}, {coord.dec.deg}), p.poly)
          AND p.energy_bandpassname = 'SPHEREx-{detector}'
    ORDER BY p.time_bounds_lower
    """
    return call_with_timeout(lambda: Irsa.query_tap(query).to_table(), timeout_s=timeout_s)


# ---- Downloading with a disk cache ---------------------------------------------
def _cache_path(url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".npz")


def measure_target(image, wcs, coord, r_in=3.0, r_out=6.0):
    """Brightness of whatever sits at coord, measured on the ORIGINAL pixels.

    Sum of the 3x3 pixels around the target (~19 arcsec box) minus the local
    background (median of the pixels 3-6 pixels away). The error comes from the
    scatter of those background pixels. Returns (flux, flux_err); NaN if the target
    is off the image or a box pixel is missing. Quick-look photometry only: a
    neighbouring star within ~10 arcsec adds its light too.
    """
    x, y = wcs.world_to_pixel(coord)
    x, y = float(x), float(y)
    ny, nx = image.shape
    xi, yi = int(round(x)), int(round(y))
    if not (1 <= xi < nx - 1 and 1 <= yi < ny - 1):
        return np.nan, np.nan
    box = image[yi - 1:yi + 2, xi - 1:xi + 2]
    if not np.all(np.isfinite(box)):
        return np.nan, np.nan
    yy, xx = np.mgrid[:ny, :nx]
    r = np.hypot(xx - x, yy - y)
    bg = image[(r >= r_in) & (r <= r_out) & np.isfinite(image)]
    if bg.size < 8:
        return np.nan, np.nan
    level = np.median(bg)
    noise = 1.4826 * np.median(np.abs(bg - level))
    return float(np.sum(box - level)), float(3.0 * noise)  # 3 = sqrt(9 pixels)


def _download_cutout(url, coord):
    with fits.open(url, cache=False) as hdulist:
        header = hdulist[1].header
        image = hdulist[1].data.astype(float)

        # Wavelength at the target position (method from the IRSA tutorial).
        spatial_wcs = WCS(header)
        x, y = spatial_wcs.world_to_pixel(coord)
        spectral_wcs = WCS(header, fobj=hdulist, key="W")
        spectral_wcs.sip = None
        wavelength, bandwidth = spectral_wcs.pixel_to_world(x, y)
        flux, flux_err = measure_target(image, spatial_wcs, coord)

        return {
            "image": image,
            "header": header.tostring(sep="\n"),
            "wavelength": float(wavelength.to(u.micrometer).value),
            "bandwidth": float(bandwidth.to(u.micrometer).value),
            "flux": flux, "flux_err": flux_err,
            "flux_unit": str(header.get("BUNIT", "unknown units")),
        }


SPECTRUM_KEYS = ("bandwidth", "flux", "flux_err", "flux_unit")


def _spectrum_fields(data):
    """Spectrum values from a cache file (older cache files may not have them)."""
    out = {}
    for k in SPECTRUM_KEYS:
        if k in data:
            out[k] = str(data[k]) if k == "flux_unit" else float(data[k])
        else:
            out[k] = "unknown units" if k == "flux_unit" else np.nan
    return out


def get_frame(url, mjd, coord, tries=3):
    """Load a frame from the disk cache, or download it (retrying if the server is busy)."""
    path = _cache_path(url)
    if os.path.exists(path):
        data = np.load(path)
        return {"mjd": float(data["mjd"]), "image": data["image"], "url": url,
                "header": str(data["header"]), "wavelength": float(data["wavelength"]),
                **_spectrum_fields(data)}

    for attempt in range(1, tries + 1):
        try:
            got = _download_cutout(url, coord)
            np.savez(path, image=got["image"], header=np.array(got["header"]),
                     wavelength=got["wavelength"], mjd=mjd,
                     **{k: got[k] for k in SPECTRUM_KEYS})
            return {"mjd": mjd, "url": url, **got}
        except Exception:
            if attempt < tries:
                time.sleep(2 * 2 ** (attempt - 1))  # 2 s, 4 s, ...
    return None


# ---- Fast loading: tiny "probe" cutouts + parallel downloads -----------------------
# A tiny cutout (~12 x 12 pixels), downloaded to read an image's wavelength AND the
# target's brightness (for the spectrum). 0.02 deg leaves room for the background ring.
PROBE_SIZE_DEG = 0.02


def with_size(url, size_deg):
    """Same cutout URL, different cutout size."""
    return f"{str(url).rsplit('size=', 1)[0]}size={size_deg}"


def get_probe(url, mjd, coord, tries=3):
    """Wavelength of one image at the target position, read from a tiny (fast) cutout.

    The wavelength is not stored in IRSA's search table, so it can only be found by
    downloading something. A 0.01 degree cutout is much quicker than a full one.
    """
    path = _cache_path("probe2:" + url)   # "probe2": these also store the brightness
    if os.path.exists(path):
        data = np.load(path)
        return {"mjd": float(data["mjd"]), "wavelength": float(data["wavelength"]),
                "url": url, **_spectrum_fields(data)}

    for attempt in range(1, tries + 1):
        try:
            got = _download_cutout(with_size(url, PROBE_SIZE_DEG), coord)
            spec = {k: got[k] for k in SPECTRUM_KEYS}
            np.savez(path, wavelength=got["wavelength"], mjd=mjd, **spec)
            return {"mjd": mjd, "wavelength": got["wavelength"], "url": url, **spec}
        except Exception:
            if attempt < tries:
                time.sleep(2 * 2 ** (attempt - 1))
    return None


def run_parallel(fn, jobs, workers=5, progress=None, deadline_s=None):
    """Run fn(*job) for every job on several threads at once.

    Returns the results in the same order as the jobs (None where a job failed or
    did not finish before the deadline). `progress(done, total)` is called from the
    calling thread as jobs finish.
    """
    results = [None] * len(jobs)
    if not jobs:
        return results
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(fn, *job): i for i, job in enumerate(jobs)}
        try:
            for done, future in enumerate(as_completed(futures, timeout=deadline_s), start=1):
                try:
                    results[futures[future]] = future.result()
                except Exception:
                    results[futures[future]] = None
                if progress:
                    progress(done, len(jobs))
        except FuturesTimeout:
            pass  # deadline reached: keep whatever has arrived
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # do not wait for stragglers
    return results


def _parallel_with_retry(fn, jobs, workers, progress, deadline_s):
    """Run in parallel; then give anything that failed one slower second chance."""
    results = run_parallel(fn, jobs, workers, progress, deadline_s)
    failed = [i for i, r in enumerate(results) if r is None]
    if failed and len(failed) < len(results):  # skip if EVERYTHING failed (no connection)
        time.sleep(3)
        again = run_parallel(fn, [jobs[i] for i in failed], 2, None, RETRY_DEADLINE_S)
        for i, r in zip(failed, again):
            results[i] = r
    return [r for r in results if r is not None], sum(r is None for r in results)


def probe_frames(urls, mjds, coord, workers=6, progress=None, deadline_s=None):
    """Read the wavelength of every candidate image. Returns (probes, number_failed).

    Each probe is {"mjd", "wavelength", "url"}; results are cached on disk forever.
    """
    jobs = [(u_, m, coord) for u_, m in zip(urls, mjds)]
    return _parallel_with_retry(get_probe, jobs, workers, progress,
                                deadline_s or PROBE_DEADLINE_S)


def download_full(chosen, coord, workers=4, progress=None, deadline_s=None):
    """Download the full-size cutouts for the chosen frames. Returns (frames, number_failed).

    Frames already downloaded stay in the cache, so pressing Fetch again resumes.
    """
    jobs = [(f["url"], f["mjd"], coord) for f in chosen]
    frames, failed = _parallel_with_retry(get_frame, jobs, workers, progress,
                                          deadline_s or DOWNLOAD_DEADLINE_S)
    return sorted(frames, key=lambda f: f["mjd"]), failed


# ---- Choosing frames ---------------------------------------------------------
def thin_by_gap(frames, min_gap):
    """Keep frames (oldest first) that are at least min_gap days after the last kept one."""
    kept = []
    for f in sorted(frames, key=lambda f: f["mjd"]):
        if not kept or f["mjd"] - kept[-1]["mjd"] >= min_gap:
            kept.append(f)
    return kept


def choose_frames(frames, target_wl=None, tol=0.10, how_many=8, min_gap=10):
    """Choose frames that are >= min_gap days apart and close in wavelength.

    tol None -> ignore wavelength. target_wl None -> pick the wavelength that gives
    the most well-spaced frames. Returns (chosen frames by date, wavelength used or None).
    """
    if not frames:
        return [], target_wl

    if tol is None:
        chosen, used = thin_by_gap(frames, min_gap), None
    elif target_wl is not None:
        near = [f for f in frames if abs(f["wavelength"] - target_wl) <= tol]
        chosen, used = thin_by_gap(near, min_gap), target_wl
    else:
        best = None
        for w in sorted({f["wavelength"] for f in frames}):
            near = [f for f in frames if abs(f["wavelength"] - w) <= tol]
            kept = thin_by_gap(near, min_gap)
            span = kept[-1]["mjd"] - kept[0]["mjd"] if kept else 0
            score = (len(kept), span)
            if best is None or score > best[0]:
                best = (score, w, kept)
        _score, used, chosen = best

    if len(chosen) > how_many:  # thin evenly, keeping the first and the last
        keep = np.linspace(0, len(chosen) - 1, how_many).astype(int)
        chosen = [chosen[i] for i in keep]
    return chosen, used


# ---- Aligning ----------------------------------------------------------------
def make_target_wcs(coord, fov_deg, scale=PIXEL_SCALE_ARCSEC):
    """A simple north-up, east-left sky grid centred on the target."""
    n = max(8, int(round(fov_deg * 3600 / scale)))
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [coord.ra.deg, coord.dec.deg]
    wcs.wcs.crpix = [n / 2 + 0.5, n / 2 + 0.5]
    wcs.wcs.cdelt = [-scale / 3600, scale / 3600]
    return wcs, (n, n)


def align_frames(frames, target_wcs, shape):
    """Resample each frame onto the common grid. These are DISPLAY copies only:
    resampling slightly smooths pixels, so never measure brightness from them."""
    aligned = []
    for f in frames:
        header = fits.Header.fromstring(f["header"], sep="\n")
        out, _footprint = reproject_interp((f["image"], WCS(header)), target_wcs,
                                           shape_out=shape)
        aligned.append({**f, "aligned": out})
    return aligned


# ---- Epoch stacks ------------------------------------------------------------
def _median_subtract(image):
    """Remove the background level (the median), so frames can be added or subtracted."""
    finite = image[np.isfinite(image)]
    return image - (np.median(finite) if finite.size else 0.0)


def group_epochs(frames, gap_days):
    """Group frames into epochs: a new epoch starts after a gap longer than gap_days."""
    groups = []
    for f in sorted(frames, key=lambda f: f["mjd"]):
        if groups and f["mjd"] - groups[-1][-1]["mjd"] <= gap_days:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def stack_epochs(aligned_frames, gap_days=45, method="mean"):
    """Co-add the aligned frames of each epoch into one deeper image per epoch.

    Each frame has its background removed first. method is "mean" or "median"
    (a median rejects things that move within an epoch; a mean keeps them).
    Returns one dict per epoch: the stacked image plus its dates and wavelength coverage.
    """
    stacks = []
    for k, group in enumerate(group_epochs(aligned_frames, gap_days), start=1):
        cube = np.array([_median_subtract(f["aligned"]) for f in group])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-empty pixels give NaN
            stacked = np.nanmedian(cube, axis=0) if method == "median" else np.nanmean(cube, axis=0)
        mjds = [f["mjd"] for f in group]
        wls = [f["wavelength"] for f in group]
        d0 = Time(min(mjds), format="mjd").iso[:10]
        d1 = Time(max(mjds), format="mjd").iso[:10]
        dates = d0 if d0 == d1 else f"{d0} to {d1}"
        stacks.append({
            "aligned": stacked, "epoch": k, "n": len(group), "url": f"epoch{k}",
            "mjd": float(np.mean(mjds)), "mjd_min": min(mjds), "mjd_max": max(mjds),
            "wavelength": float(np.mean(wls)), "wl_min": min(wls), "wl_max": max(wls),
            "urls": [f["url"] for f in group], "dates": dates,
            "label": f"Epoch {k}: {dates} ({len(group)} frames)",
        })
    return stacks


# ---- False-colour picture from one detector ------------------------------------
def colour_channels(frames, detector):
    """Split frames into 3 wavelength groups across the detector band.

    Fixed edges (the band cut in thirds), so every epoch uses the same colours.
    Returns [(name, lo_um, hi_um, frames_in_group)] for blue, green, red.
    """
    lo, hi = DETECTOR_BANDS_UM[detector]
    edges = np.linspace(lo, hi, 4)
    out = []
    for name, a, b in zip(("blue", "green", "red"), edges[:-1], edges[1:]):
        group = [f for f in frames if f.get("wavelength") is not None
                 and a <= f["wavelength"] < b + (1e-9 if name == "red" else 0)]
        out.append((name, float(a), float(b), group))
    return out


def colour_image(frames, detector, width_px=STAGE_PX, label=None):
    """A false-colour picture: the shortest third of the detector's wavelengths shown
    as blue, the middle as green, the longest as red. Each group's aligned frames are
    added together (background removed). Each colour is stretched on its own, so
    ordinary stars come out roughly white and an object whose spectrum differs from
    theirs gets a tint. Uses the north-up display copies: for looking, not measuring.

    Returns (PIL image, channel summary); None if fewer than 2 colours have frames.
    """
    channels = colour_channels(frames, detector)
    planes, summary = [], []
    for name, a, b, group in channels:
        summary.append({"colour": name, "from_um": a, "to_um": b, "frames": len(group)})
        if not group:
            planes.append(None)
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            planes.append(np.nanmean([_median_subtract(f["aligned"]) for f in group], axis=0))
    if sum(p is not None for p in planes) < 2:
        return None, summary

    # Colour-preserving asinh stretch (Lupton et al. 2004): squash the brightness but
    # keep each pixel's colour ratios, so bright objects keep their tint instead of
    # all turning white. First each colour is scaled by its own bright-star level, so
    # ordinary stars come out roughly white.
    shape = next(p for p in planes if p is not None).shape
    chans = np.zeros(shape + (3,))
    for i, plane in enumerate(reversed(planes)):  # RGB order: red, green, blue
        if plane is None:
            continue
        p = np.nan_to_num(plane, nan=0.0)
        finite = p[p != 0] if np.any(p != 0) else p.ravel()
        level = np.percentile(finite, 99.5)
        chans[..., i] = p / max(level, 1e-12)
    intensity = chans.mean(axis=2)
    finite = intensity.ravel()
    soft = 3 * 1.4826 * np.median(np.abs(finite - np.median(finite)))  # ~3x the noise
    soft = max(soft, 1e-12)
    stretched = np.arcsinh(np.clip(intensity, 0, None) / soft)
    top = max(np.percentile(stretched, 99.9), 1e-12)
    factor = np.where(intensity > 0, (stretched / top) / np.maximum(intensity, 1e-12), 0)
    rgb = np.clip(chans, 0, None) * factor[..., None]
    rgb /= np.maximum(1.0, rgb.max(axis=2))[..., None]   # clip without changing the hue
    arr = np.flipud((rgb * 255).astype(np.uint8))
    scale = max(1, int(round(width_px / arr.shape[1])))
    img = Image.fromarray(arr, "RGB").resize((arr.shape[1] * scale, arr.shape[0] * scale),
                                             Image.NEAREST)
    if label:
        ImageDraw.Draw(img).text((6, 6), label, fill=(255, 255, 255), font=_font(_label_size(img)),
                                 stroke_width=2, stroke_fill=(0, 0, 0))
    return img, summary


# ---- Spectrum on the fly ------------------------------------------------------
def spectrum_points(frames, epoch_gap=45):
    """One spectrum point per frame: target brightness at that frame's wavelength.

    Uses the brightness measured on the original pixels when the frame (or its tiny
    probe) was downloaded. Frames are grouped into epochs so changes over time
    (variable stars) can be told apart from the spectrum's shape.
    Returns a list of dicts sorted by wavelength.
    """
    good = [f for f in frames
            if f.get("wavelength") is not None and np.isfinite(f.get("flux", np.nan))]
    epoch_of = {}
    for k, group in enumerate(group_epochs(good, epoch_gap), start=1):
        for f in group:
            epoch_of[f["url"]] = k
    pts = [{"wavelength": f["wavelength"], "bandwidth": f.get("bandwidth", np.nan),
            "flux": f["flux"], "flux_err": f.get("flux_err", np.nan),
            "unit": f.get("flux_unit", "unknown units"), "mjd": f["mjd"],
            "date": Time(f["mjd"], format="mjd").iso[:10], "epoch": epoch_of[f["url"]]}
           for f in good]
    return sorted(pts, key=lambda p: p["wavelength"])


def bin_spectrum(points, detector, n_bins=CHANNELS_PER_DETECTOR):
    """Median brightness in n_bins equal wavelength bins across the detector band
    (17 bins ~ SPHEREx's 102 bands / 6 detectors). Returns a list of dicts."""
    lo, hi = DETECTOR_BANDS_UM[detector]
    edges = np.linspace(lo, hi, n_bins + 1)
    wl = np.array([p["wavelength"] for p in points])
    fl = np.array([p["flux"] for p in points])
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (wl >= a) & (wl < b)
        if sel.any():
            out.append({"wavelength": float((a + b) / 2), "flux": float(np.median(fl[sel])),
                        "n": int(sel.sum())})
    return out


# ---- Gaia DR3 overlay ---------------------------------------------------------
# Gaia is a star catalogue (positions, brightness, motions), not an image archive, so
# it is shown as markers on the SPHEREx pictures plus a details panel.
# Source: the ESA Gaia archive via astroquery.gaia. If ESA does not answer (on 15 Nov 2026
# it was unreachable from our network), the same gaiadr3.gaia_source table is read from
# the ARI Heidelberg mirror. DR3 positions are for epoch 2016.0, so each star is moved
# to the date of the frame it is drawn on, using its proper motion.
GAIA_RELEASE = "Gaia DR3"
GAIA_ESA_TIMEOUT_S = 15
GAIA_ARI_TIMEOUT_S = 30
GAIA_ARI_TAP = "https://gaia.ari.uni-heidelberg.de/tap"
GAIA_MAX_ROWS = 2000       # brightest first; very crowded fields lose their faintest stars
HIGH_PM_MAS_YR = 100       # stars moving faster than this get a different marker colour
# How close a Gaia star must be to the centre to count as "the target". Kept tight on
# purpose: at 12" the WISE 0855 view (WISE 0855 is not in Gaia) wrongly matched an
# unrelated G=16 star 8" away. Quick-target positions agree with Gaia to < 2".
TARGET_MATCH_ARCSEC = 4
_esa_down_until = 0.0      # after an ESA failure, skip ESA for a few minutes


def _gaia_query(coord, radius_deg):
    return f"""
    SELECT TOP {GAIA_MAX_ROWS} source_id, ra, dec, ref_epoch, pmra, pmdec,
           parallax, parallax_error, phot_g_mean_mag, bp_rp, radial_velocity,
           teff_gspphot, ruwe
    FROM gaiadr3.gaia_source
    WHERE 1 = CONTAINS(POINT('ICRS', ra, dec),
                       CIRCLE('ICRS', {coord.ra.deg}, {coord.dec.deg}, {radius_deg}))
    ORDER BY phot_g_mean_mag
    """


def _query_esa(query):
    from astroquery.gaia import Gaia  # imported here so the app starts without it
    return Gaia.launch_job(query).get_results()


def _query_ari(query):
    import pyvo  # installed together with astroquery
    return pyvo.dal.TAPService(GAIA_ARI_TAP).search(query).to_table()


def find_gaia(coord, fov_deg):
    """Gaia DR3 stars in the field, brightest first, as plain numpy arrays.

    The search radius covers the corners of the square view. Missing values (common for
    faint stars) become NaN; missing proper motions are set to 0 and flagged in "has_pm".
    "server" says which archive answered.
    """
    global _esa_down_until
    query = _gaia_query(coord, fov_deg * 0.75)  # half-diagonal is 0.707 x width + margin
    table, server = None, None
    if time.time() >= _esa_down_until:
        try:
            table = call_with_timeout(_query_esa, query, timeout_s=GAIA_ESA_TIMEOUT_S)
            server = "ESA Gaia archive"
        except Exception:
            _esa_down_until = time.time() + 600
    if table is None:
        table = call_with_timeout(_query_ari, query, timeout_s=GAIA_ARI_TIMEOUT_S)
        server = "ARI Heidelberg Gaia mirror"

    table.rename_columns(table.colnames, [c.lower() for c in table.colnames])

    def col(name, fill=np.nan):
        c = table[name]
        return np.asarray(c.filled(fill) if hasattr(c, "filled") else c, dtype=float)

    pmra, pmdec = col("pmra"), col("pmdec")
    has_pm = np.isfinite(pmra) & np.isfinite(pmdec)
    return {
        "source_id": [str(int(x)) for x in table["source_id"]],
        "ra": col("ra"), "dec": col("dec"), "ref_epoch": col("ref_epoch", 2016.0),
        "pmra": np.where(has_pm, pmra, 0.0), "pmdec": np.where(has_pm, pmdec, 0.0),
        "has_pm": has_pm, "gmag": col("phot_g_mean_mag"), "bp_rp": col("bp_rp"),
        "parallax": col("parallax"), "parallax_error": col("parallax_error"),
        "radial_velocity": col("radial_velocity"), "teff": col("teff_gspphot"),
        "ruwe": col("ruwe"), "server": server, "truncated": len(table) >= GAIA_MAX_ROWS,
    }


def gaia_at(gaia, mjd):
    """Gaia positions moved (in a straight line) to the date mjd. Returns (ra, dec) in deg.

    A straight-line move is accurate to well under one SPHEREx pixel over a few decades,
    even for Barnard's Star.
    """
    years = Time(mjd, format="mjd").jyear - gaia["ref_epoch"]
    dec = gaia["dec"] + gaia["pmdec"] * years / 3.6e6
    ra = gaia["ra"] + gaia["pmra"] * years / 3.6e6 / np.cos(np.radians(dec))
    return ra, dec


def nearest_gaia(gaia, coord, mjd, max_arcsec=TARGET_MATCH_ARCSEC):
    """Index of the Gaia star closest to coord at date mjd, or None if none is that close.

    Returns (index or None, separation in arcsec or None).
    """
    if len(gaia["ra"]) == 0:
        return None, None
    ra, dec = gaia_at(gaia, mjd)
    sep = SkyCoord(ra=ra * u.deg, dec=dec * u.deg).separation(coord).arcsec
    i = int(np.argmin(sep))
    return (i, float(sep[i])) if sep[i] <= max_arcsec else (None, float(sep[i]))


def gaia_details(gaia, i):
    """Readable facts about Gaia star i, as a list of (label, value) pairs."""
    def num(key, fmt):
        v = gaia[key][i]
        return fmt.format(v) if np.isfinite(v) else "not measured"

    plx, plx_err = gaia["parallax"][i], gaia["parallax_error"][i]
    if np.isfinite(plx) and plx > 0 and np.isfinite(plx_err) and plx / plx_err >= 5:
        pc = 1000.0 / plx
        distance = f"{pc:.1f} pc ({pc * 3.2616:.1f} light-years), from parallax"
    else:
        distance = "too uncertain to quote"
    pm = np.hypot(gaia["pmra"][i], gaia["pmdec"][i])
    pm_text = (f"{pm:.1f} mas/yr  (RA {gaia['pmra'][i]:+.1f}, Dec {gaia['pmdec'][i]:+.1f})"
               if gaia["has_pm"][i] else "not measured")
    return [
        ("Gaia DR3 source", gaia["source_id"][i]),
        ("RA, Dec (epoch 2016.0)", f"{gaia['ra'][i]:.6f}, {gaia['dec'][i]:+.6f}"),
        ("G magnitude (visible)", num("gmag", "{:.2f}")),
        ("BP-RP colour (bigger = redder)", num("bp_rp", "{:.2f}")),
        ("Distance", distance),
        ("Proper motion", pm_text),
        ("Radial velocity", num("radial_velocity", "{:.1f} km/s")),
        ("Temperature (Gaia GSP-Phot estimate)", num("teff", "{:.0f} K")),
        ("RUWE (above 1.4 may mean a binary or bad fit)", num("ruwe", "{:.2f}")),
    ]


# ---- WISE infrared overlay (CatWISE2020) ------------------------------------------
# WISE observed the whole sky at 3.4 um (W1) and 4.6 um (W2), the same wavelengths as
# SPHEREx's D4-D6, and it sees the cold, red objects Gaia misses. CatWISE2020 combines
# WISE and NEOWISE data and includes proper motions. Read from NOIRLab's Astro Data Lab
# (IRSA's own copy, table catwise_2020, was unreachable when this was written).
# Positions (ra_pm, dec_pm) are for MJD 57170 = 2015.405; pmra/pmdec are in arcsec/yr.
WISE_TAP = "https://datalab.noirlab.edu/tap"
WISE_TIMEOUT_S = 40
WISE_REF_EPOCH = 2015.405
WISE_MAX_ROWS = 2000
WISE_PM_MIN_SIGMA = 3.0   # use a motion only if it is at least 3x its uncertainty


def find_wise(coord, fov_deg):
    """CatWISE2020 sources in the field, brightest in W2 first, as numpy arrays (same
    position/motion keys as find_gaia, so gaia_at and nearest_gaia work on it too)."""
    import pyvo  # installed together with astroquery

    radius = fov_deg * 0.75
    query = f"""
    SELECT TOP {WISE_MAX_ROWS} source_name, ra_pm, dec_pm, pmra, pmdec, sigpmra, sigpmdec,
           w1mpro, w2mpro, cc_flags, ab_flags
    FROM catwise2020.main
    WHERE 't' = q3c_radial_query(ra, dec, {coord.ra.deg}, {coord.dec.deg}, {radius})
    ORDER BY w2mpro
    """
    table = call_with_timeout(lambda: pyvo.dal.TAPService(WISE_TAP).search(query).to_table(),
                              timeout_s=WISE_TIMEOUT_S)

    def col(name):
        c = table[name]
        return np.asarray(c.filled(np.nan) if hasattr(c, "filled") else c, dtype=float)

    pmra, pmdec = col("pmra"), col("pmdec")
    sra, sdec = col("sigpmra"), col("sigpmdec")
    with np.errstate(invalid="ignore", divide="ignore"):
        signif = np.hypot(pmra / sra, pmdec / sdec)
    has_pm = np.isfinite(signif) & (signif >= WISE_PM_MIN_SIGMA)
    w1, w2 = col("w1mpro"), col("w2mpro")
    cc = ["" if x is None else str(x).strip() for x in table["cc_flags"]]
    ab = ["" if x is None else str(x).strip() for x in table["ab_flags"]]
    # AllWISE contamination flags (cc_flags): one character per band, 0 = clean;
    # D = diffraction spike, P = persistence, H = bright-star halo, O = optical ghost.
    # Upper case = the source is probably spurious; lower case = real but contaminated.
    flagged = np.array([any(ch in "DPHO" for ch in f) for f in cc], dtype=bool)
    return {
        "name": [f"CWISE {str(x).strip()}" for x in table["source_name"]],
        "ra": col("ra_pm"), "dec": col("dec_pm"),
        "ref_epoch": np.full(len(table), WISE_REF_EPOCH),
        "pmra": np.where(has_pm, pmra * 1000.0, 0.0),    # -> mas/yr, like Gaia
        "pmdec": np.where(has_pm, pmdec * 1000.0, 0.0),
        "has_pm": has_pm, "w1": w1, "w2": w2, "w1_w2": w1 - w2,
        "cc_flags": cc, "ab_flags": ab, "flagged": flagged,
        "truncated": len(table) >= WISE_MAX_ROWS,
    }


def wise_details(wise, i):
    """Readable facts about WISE source i, as a list of (label, value) pairs."""
    def mag(v):
        return f"{v:.2f}" if np.isfinite(v) else "not measured"

    colour = wise["w1_w2"][i]
    if np.isfinite(colour):
        hint = (" (very red: typical of cold brown dwarfs)" if colour >= 1.0
                else " (red)" if colour >= 0.5 else " (ordinary star colour)")
        colour_text = f"{colour:.2f}{hint}"
    else:
        colour_text = "not measured"
    pm = np.hypot(wise["pmra"][i], wise["pmdec"][i])
    pm_text = (f"{pm:.0f} mas/yr  (RA {wise['pmra'][i]:+.0f}, Dec {wise['pmdec'][i]:+.0f})"
               if wise["has_pm"][i] else "not significant")
    return [
        ("CatWISE2020 source", wise["name"][i]),
        ("RA, Dec (epoch 2015.4)", f"{wise['ra'][i]:.6f}, {wise['dec'][i]:+.6f}"),
        ("W1 magnitude (3.4 µm)", mag(wise["w1"][i])),
        ("W2 magnitude (4.6 µm)", mag(wise["w2"][i])),
        ("W1 − W2 colour", colour_text),
        ("Proper motion", pm_text),
        ("Contamination flags (cc_flags)", _cc_text(wise["cc_flags"][i])),
    ]


_CC_MEANING = {"d": "diffraction spike", "p": "persistence", "h": "bright-star halo",
               "o": "optical ghost"}


def _cc_text(flags):
    if not flags or set(flags) <= {"0"}:
        return f"{flags or '-'} (clean)"
    parts = sorted({("likely spurious: " if ch.isupper() else "contaminated: ")
                    + _CC_MEANING[ch.lower()] for ch in flags if ch.lower() in _CC_MEANING})
    return f"{flags} ({'; '.join(parts)})"


def add_wise_markers(img, wise, mjd, target_wcs, n_pix, mag_limit=17.0, highlight=None):
    """Draw a diamond on every CatWISE2020 source (lavender; orange if fast-moving;
    grey if its contamination flags say it may be an artefact).
    highlight = index of one source to mark with a larger magenta diamond."""
    img = img.convert("RGB")
    ra, dec = gaia_at(wise, mjd)
    x, y = target_wcs.world_to_pixel_values(ra, dec)
    pm = np.hypot(wise["pmra"], wise["pmdec"])
    keep = np.isfinite(wise["w2"]) & (wise["w2"] <= mag_limit)
    scale = img.width / n_pix
    r = max(3.0, 1.0 * scale)
    draw = ImageDraw.Draw(img)

    def diamond(cx, cy, size, colour, width):
        draw.polygon([(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)],
                     outline=colour, width=width)

    for k in np.flatnonzero(keep):
        if k == highlight:
            continue
        cx, cy = (x[k] + 0.5) * scale, (n_pix - 1 - y[k] + 0.5) * scale
        if -r <= cx <= img.width + r and -r <= cy <= img.height + r:
            fast = pm[k] > HIGH_PM_MAS_YR
            colour = ((140, 140, 160) if wise["flagged"][k]       # contaminated: grey
                      else (255, 150, 0) if fast else (185, 181, 255))
            diamond(cx, cy, r, colour, 2 if fast and not wise["flagged"][k] else 1)
    if highlight is not None:
        cx = (x[highlight] + 0.5) * scale
        cy = (n_pix - 1 - y[highlight] + 0.5) * scale
        diamond(cx, cy, r * 2.2, (255, 0, 255), 2)
    return img


def add_gaia_markers(img, gaia, mjd, target_wcs, n_pix, mag_limit=21.0, highlight=None):
    """Draw a circle around every Gaia star on a displayed picture (north up, east left).

    Cyan = ordinary star; orange = fast-moving star (> HIGH_PM_MAS_YR mas/yr).
    highlight = index of one star (e.g. the target) to mark with a magenta box,
    whatever its magnitude.
    """
    img = img.convert("RGB")
    ra, dec = gaia_at(gaia, mjd)
    x, y = target_wcs.world_to_pixel_values(ra, dec)
    pm = np.hypot(gaia["pmra"], gaia["pmdec"])
    keep = np.isfinite(gaia["gmag"]) & (gaia["gmag"] <= mag_limit)
    scale = img.width / n_pix
    radius = max(3.0, 0.9 * scale)
    draw = ImageDraw.Draw(img)

    def centre(k):
        # +0.5: pixel centres; the picture is flipped so north is up
        return (x[k] + 0.5) * scale, (n_pix - 1 - y[k] + 0.5) * scale

    for k in np.flatnonzero(keep):
        if k == highlight:
            continue
        cx, cy = centre(k)
        if -radius <= cx <= img.width + radius and -radius <= cy <= img.height + radius:
            fast = pm[k] > HIGH_PM_MAS_YR
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                         outline=(255, 150, 0) if fast else (0, 220, 255),
                         width=2 if fast else 1)
    if highlight is not None:
        cx, cy = centre(highlight)
        r = radius * 1.8
        draw.rectangle([cx - r, cy - r, cx + r, cy + r], outline=(255, 0, 255), width=2)
    return img


# ---- Rendering ---------------------------------------------------------------
def to_uint8(image):
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return np.zeros(image.shape, dtype=np.uint8)
    vmin, vmax = ZScaleInterval().get_limits(finite)
    scaled = np.clip((np.nan_to_num(image, nan=vmin) - vmin) / max(vmax - vmin, 1e-12), 0, 1)
    return (scaled * 255).astype(np.uint8)


def _font(size=16):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


DEMO_WATERMARK = "SYNTHETIC DEMO DATA, made with AI-assisted code"


def add_watermark(img, text=DEMO_WATERMARK):
    """A visible watermark across the bottom of a picture (NASA Space Apps rule: images
    made with SI/AI help must say so). Used on every demo-data picture."""
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    size = max(12, _label_size(img) - 4)
    font = _font(size)
    pad = max(4, size // 3)
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    y = img.height - h - 3 * pad
    draw.rectangle([0, y - pad, img.width, img.height], fill=(1, 0, 58))
    draw.text(((img.width - w) // 2, y), text, fill=(232, 224, 133), font=font)
    return img


def frame_image(f, width_px=STAGE_PX, label=True):
    """One aligned frame as an enlarged picture (north up, east left) with a date label."""
    arr = np.flipud(to_uint8(f["aligned"]))
    scale = max(1, int(round(width_px / arr.shape[1])))
    img = Image.fromarray(arr).resize((arr.shape[1] * scale, arr.shape[0] * scale),
                                      Image.NEAREST)
    if label:
        text = f.get("label") or (
            f"{Time(f['mjd'], format='mjd').iso[:10]}   {f['wavelength']:.2f} um")
        ImageDraw.Draw(img).text((6, 6), text, fill=255, font=_font(_label_size(img)),
                                 stroke_width=2, stroke_fill=0)
    return img


def make_gif_bytes(aligned, ms_per_frame=500, width_px=STAGE_PX, decorate=None):
    """Render the aligned frames as a looping animated GIF (returned as bytes).

    decorate(picture, frame) -> picture, if given, draws extras (e.g. Gaia markers).
    """
    pics = [frame_image(f, width_px) for f in aligned]
    if decorate:
        pics = [decorate(pic, f) for pic, f in zip(pics, aligned)]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:],
                 duration=int(ms_per_frame), loop=0)
    return buf.getvalue()


def combine_image(a, b, op="difference", stretch_sigma=5.0, width_px=STAGE_PX):
    """Subtract or add two aligned images (each with its background removed first).

    op "difference": shows B - A. White = no change, red = brighter in B,
    blue = fainter in B, grey = no data. Something that moved appears as a red
    spot next to a blue spot. op "sum": shows A + B in greyscale.
    Returns a PIL image (north up, east left).
    """
    a0, b0 = _median_subtract(a), _median_subtract(b)

    if op == "sum":
        arr = np.flipud(to_uint8(a0 + b0))
        scale = max(1, int(round(width_px / arr.shape[1])))
        return Image.fromarray(arr).resize((arr.shape[1] * scale, arr.shape[0] * scale),
                                           Image.NEAREST)

    d = b0 - a0
    finite = d[np.isfinite(d)]
    if finite.size:
        sigma = 1.4826 * np.median(np.abs(finite - np.median(finite)))  # robust noise level
    else:
        sigma = 1.0
    sigma = max(float(sigma), 1e-12)
    v = np.clip(np.nan_to_num(d, nan=0.0) / (stretch_sigma * sigma), -1, 1)
    pos, neg = np.clip(v, 0, 1), np.clip(-v, 0, 1)
    rgb = np.stack([255 * (1 - neg), 255 * (1 - np.abs(v)), 255 * (1 - pos)], axis=-1)
    rgb[~np.isfinite(d)] = 90  # grey where there is no data
    arr = np.flipud(rgb.astype(np.uint8))
    scale = max(1, int(round(width_px / arr.shape[1])))
    return Image.fromarray(arr).resize((arr.shape[1] * scale, arr.shape[0] * scale), Image.NEAREST)

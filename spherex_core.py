"""
spherex_core.py -- the "engine" behind the SPHEREx Blink web app (app.py).

It contains no web-page code, only the science and data plumbing:
  * parse_coord      read RA/Dec typed by a person
  * find_images      ask IRSA which SPHEREx images cover a position
  * probe_frames     read every candidate image's wavelength from tiny, fast cutouts
  * download_full    download the full-size cutouts of ONLY the chosen frames, in parallel
  * get_frame        download one cutout (disk cache + retries)
  * choose_frames    pick frames that are far apart in time and similar in wavelength
  * align_frames     resample every frame onto one common north-up sky grid
  * make_gif_bytes   render the aligned frames as an animated GIF

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


def find_images(coord, cutout_deg, detector="D2", timeout_s=None):
    """Ask IRSA for every SPHEREx image covering this position (oldest first).

    Each row has a cutout URL ("uri") and the observation time ("time_bounds_lower", MJD).
    """
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
    return call_with_timeout(lambda: Irsa.query_tap(query).to_table(),
                             timeout_s=timeout_s or TAP_TIMEOUT_S)


# ---- Downloading with a disk cache ---------------------------------------------
def _cache_path(url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".npz")


def _download_cutout(url, coord):
    with fits.open(url, cache=False) as hdulist:
        header = hdulist[1].header
        image = hdulist[1].data.astype(float)

        # Wavelength at the target position (method from the IRSA tutorial).
        spatial_wcs = WCS(header)
        x, y = spatial_wcs.world_to_pixel(coord)
        spectral_wcs = WCS(header, fobj=hdulist, key="W")
        spectral_wcs.sip = None
        wavelength, _bandwidth = spectral_wcs.pixel_to_world(x, y)

        return {
            "image": image,
            "header": header.tostring(sep="\n"),
            "wavelength": float(wavelength.to(u.micrometer).value),
        }


def get_frame(url, mjd, coord, tries=3):
    """Load a frame from the disk cache, or download it (retrying if the server is busy)."""
    path = _cache_path(url)
    if os.path.exists(path):
        data = np.load(path)
        return {"mjd": float(data["mjd"]), "image": data["image"], "url": url,
                "header": str(data["header"]), "wavelength": float(data["wavelength"])}

    for attempt in range(1, tries + 1):
        try:
            got = _download_cutout(url, coord)
            np.savez(path, image=got["image"], header=np.array(got["header"]),
                     wavelength=got["wavelength"], mjd=mjd)
            return {"mjd": mjd, "url": url, **got}
        except Exception:
            if attempt < tries:
                time.sleep(2 * 2 ** (attempt - 1))  # 2 s, 4 s, ...
    return None


# ---- Fast loading: tiny "probe" cutouts + parallel downloads -----------------------
PROBE_SIZE_DEG = 0.01  # a tiny cutout, downloaded only to read an image's wavelength


def with_size(url, size_deg):
    """Same cutout URL, different cutout size."""
    return f"{str(url).rsplit('size=', 1)[0]}size={size_deg}"


def get_probe(url, mjd, coord, tries=3):
    """Wavelength of one image at the target position, read from a tiny (fast) cutout.

    The wavelength is not stored in IRSA's search table, so it can only be found by
    downloading something. A 0.01 degree cutout is much quicker than a full one.
    """
    path = _cache_path("probe:" + url)
    if os.path.exists(path):
        data = np.load(path)
        return {"mjd": float(data["mjd"]), "wavelength": float(data["wavelength"]), "url": url}

    for attempt in range(1, tries + 1):
        try:
            got = _download_cutout(with_size(url, PROBE_SIZE_DEG), coord)
            np.savez(path, wavelength=got["wavelength"], mjd=mjd)
            return {"mjd": mjd, "wavelength": got["wavelength"], "url": url}
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
    if failed and len(failed) < len(results):  # skip if EVERYTHING failed: server is down
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


def frame_image(f, width_px=480, label=True):
    """One aligned frame as an enlarged picture (north up, east left) with a date label."""
    arr = np.flipud(to_uint8(f["aligned"]))
    scale = max(1, int(round(width_px / arr.shape[1])))
    img = Image.fromarray(arr).resize((arr.shape[1] * scale, arr.shape[0] * scale),
                                      Image.NEAREST)
    if label:
        text = f.get("label") or (
            f"{Time(f['mjd'], format='mjd').iso[:10]}   {f['wavelength']:.2f} um")
        ImageDraw.Draw(img).text((6, 6), text, fill=255, font=_font(16),
                                 stroke_width=2, stroke_fill=0)
    return img


def make_gif_bytes(aligned, ms_per_frame=500, width_px=480):
    """Render the aligned frames as a looping animated GIF (returned as bytes)."""
    pics = [frame_image(f, width_px) for f in aligned]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:],
                 duration=int(ms_per_frame), loop=0)
    return buf.getvalue()


def combine_image(a, b, op="difference", stretch_sigma=5.0, width_px=480):
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

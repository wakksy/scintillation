"""
demo_data.py -- FAKE SPHEREx-like data for trying the app offline, at any time.

AI use (NASA Space Apps SI disclosure): this file was written with help from
Claude (Anthropic), used through Claude Code, and reviewed and tested by the
Scintillation team. The idea and design decisions are the team's. See README.md.
Pictures made from this data are synthetic and carry a visible watermark.

Nothing here is a real observation. It invents, for any RA/Dec:
  * a fixed field of background stars,
  * a "brown dwarf candidate" at the centre that moves 10 arcsec per year (about 1.6
    pixels between the first and last epoch: a red/blue pair in the difference image),
    gets ~15% brighter or fainter between epochs, and has a made-up spectrum with
    dips at 1.4, 2.3 and 3.3 um (where water, CO and methane absorb in real cold
    objects),
  * a fast "asteroid" that crosses the field during the second epoch only,
  * 3 epochs (May 2025, Nov 2025, May 2026) of 12 frames each, half of them rotated
    180 degrees like real SPHEREx frames, each at a random wavelength in the band.

It offers the same three functions the app uses from spherex_core (find_images,
probe_frames, download_full), so the whole pipeline (wavelength matching, epoch
stacks, alignment, GIF, difference images, spectrum) runs on it unchanged. The
brightness for the spectrum is measured with the real spherex_core.measure_target.
"""

import hashlib
from functools import lru_cache

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astropy.wcs import WCS

import spherex_core as core

EPOCH_MJDS = (60815.0, 60999.0, 61180.0)   # 2025-05-20, 2025-11-20, 2026-05-20
FRAMES_PER_EPOCH = 12
# (RA*cos(dec), Dec): 10 arcsec/yr in total, about Barnard's Star. Faster movers drift
# out of the 3x3-pixel spectrum box (tried 25"/yr: the spectrum was ruined).
MOVER_PM_ARCSEC_YR = (-8.0, 6.0)
ASTEROID_DEG_PER_DAY = 0.04
NOISE = 1.0
PSF_SIGMA_PX = 0.8
PIXEL = core.PIXEL_SCALE_ARCSEC


def _seed(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _parse(url):
    """Frame number, detector, centre and size out of a demo URL."""
    path, query = url.split("?", 1)
    k = int(path.rsplit("frame", 1)[1])
    detector = path.rsplit("/", 2)[1]
    args = dict(p.split("=") for p in query.split("&"))
    ra, dec = args["center"].rstrip("d").split(",")
    return k, detector, float(ra), float(dec), float(args["size"])


def _frame_info(k, ra0, dec0, detector):
    """Date, wavelength and orientation of demo frame k (always the same for k)."""
    rng = np.random.default_rng(_seed("frame", k, round(ra0, 4), round(dec0, 4), detector))
    epoch = k // FRAMES_PER_EPOCH
    mjd = EPOCH_MJDS[epoch] + (k % FRAMES_PER_EPOCH) * 0.9 + rng.uniform(0, 0.3)
    lo, hi = core.DETECTOR_BANDS_UM[detector]
    wavelength = float(rng.uniform(lo + 0.01, hi - 0.01))
    return {"mjd": float(mjd), "wavelength": wavelength, "epoch": epoch,
            "rotated": bool(k % 2), "dx": rng.uniform(-1, 1), "dy": rng.uniform(-1, 1)}


def mover_spectrum(wavelength_um):
    """Made-up spectrum of the demo mover (arbitrary units)."""
    w = np.asarray(wavelength_um, dtype=float)
    shape = 150.0 * (w / 1.0) ** -0.8
    for centre, width, depth in ((1.40, 0.06, 0.45), (2.30, 0.08, 0.35), (3.30, 0.10, 0.55)):
        shape = shape * (1 - depth * np.exp(-0.5 * ((w - centre) / width) ** 2))
    return shape


def _stars(ra0, dec0, radius_deg):
    """A fixed random star field around (ra0, dec0)."""
    rng = np.random.default_rng(_seed("stars", round(ra0, 3), round(dec0, 3)))
    n = int(40 + 600 * radius_deg)
    r = radius_deg * np.sqrt(rng.uniform(0, 1, n))
    t = rng.uniform(0, 2 * np.pi, n)
    dec = dec0 + r * np.sin(t)
    ra = ra0 + r * np.cos(t) / np.cos(np.radians(dec0))
    flux = 8 * rng.pareto(1.3, n) + 4
    keep = np.hypot((ra - ra0) * np.cos(np.radians(dec0)), dec - dec0) * 3600 > 25
    return ra[keep], dec[keep], flux[keep]   # nothing within 25" of the centre


def _frame_wcs(ra, dec, size_deg, info):
    n = max(8, int(round(size_deg * 3600 / PIXEL)))
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [n / 2 + 0.5 + info["dx"], n / 2 + 0.5 + info["dy"]]
    s = PIXEL / 3600
    w.wcs.cd = [[s, 0], [0, -s]] if info["rotated"] else [[-s, 0], [0, s]]  # 180 deg turn
    return w, n


def _add_source(img, x, y, flux):
    ny, nx = img.shape
    if not (-3 <= x < nx + 3 and -3 <= y < ny + 3):
        return
    x0, x1 = max(0, int(x) - 4), min(nx, int(x) + 5)
    y0, y1 = max(0, int(y) - 4), min(ny, int(y) + 5)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    img[y0:y1, x0:x1] += flux / (2 * np.pi * PSF_SIGMA_PX ** 2) * np.exp(
        -0.5 * ((xx - x) ** 2 + (yy - y) ** 2) / PSF_SIGMA_PX ** 2)


@lru_cache(maxsize=512)
def _render(url):
    """Make one fake frame. Returns the same dict get_frame returns for real data."""
    k, detector, ra0, dec0, size = _parse(url)
    info = _frame_info(k, ra0, dec0, detector)
    wcs, n = _frame_wcs(ra0, dec0, size, info)
    rng = np.random.default_rng(_seed("noise", url))
    img = rng.normal(0, NOISE, (n, n)) + 5.0 + rng.uniform(-1, 1)

    # Background stars (a little fainter at longer wavelengths).
    sra, sdec, sflux = _stars(ra0, dec0, 0.12)
    xs, ys = wcs.world_to_pixel_values(sra, sdec)
    dim = (info["wavelength"] / 1.3) ** -0.3
    for x, y, f in zip(xs, ys, sflux):
        _add_source(img, x, y, f * dim)

    # The moving, varying "brown dwarf candidate".
    years = (info["mjd"] - EPOCH_MJDS[1]) / 365.25
    mdec = dec0 + MOVER_PM_ARCSEC_YR[1] * years / 3600
    mra = ra0 + MOVER_PM_ARCSEC_YR[0] * years / 3600 / np.cos(np.radians(dec0))
    variability = (1.0, 1.15, 0.9)[info["epoch"]]
    mx, my = wcs.world_to_pixel_values(mra, mdec)
    _add_source(img, mx, my, float(mover_spectrum(info["wavelength"])) * variability)

    # A fast "asteroid", only during the second epoch.
    if info["epoch"] == 1:
        days = info["mjd"] - (EPOCH_MJDS[1] + 5)
        ax, ay = wcs.world_to_pixel_values(
            ra0 + (-0.05 + ASTEROID_DEG_PER_DAY * days) / np.cos(np.radians(dec0)),
            dec0 + 0.02 - 0.3 * ASTEROID_DEG_PER_DAY * days)
        _add_source(img, ax, ay, 90.0)

    header = wcs.to_header()
    header["BUNIT"] = "demo units"
    centre = SkyCoord(ra=ra0 * u.deg, dec=dec0 * u.deg)
    flux, flux_err = core.measure_target(img, wcs, centre)
    date = Time(info["mjd"], format="mjd").iso[:10]
    return {"mjd": info["mjd"], "url": url, "image": img,
            "header": header.tostring(sep="\n"), "wavelength": info["wavelength"],
            "bandwidth": info["wavelength"] / 40, "flux": flux, "flux_err": flux_err,
            "flux_unit": "demo units",
            "label": f"DEMO  {date}   {info['wavelength']:.2f} um"}


# ---- The three functions the app calls (same shapes as spherex_core) -------------
def find_images(coord, cutout_deg, detector="D2"):
    """Fake search result: (urls, mjds). Epochs 1-2 are labelled dr1, epoch 3 qr3."""
    urls, mjds = [], []
    for k in range(len(EPOCH_MJDS) * FRAMES_PER_EPOCH):
        info = _frame_info(k, coord.ra.deg, coord.dec.deg, detector)
        release = "dr1" if info["epoch"] < 2 else "qr3"
        urls.append(f"demo://irsa/spherex/{release}/{detector}/frame{k:03d}"
                    f"?center={coord.ra.deg},{coord.dec.deg}d&size={cutout_deg}")
        mjds.append(info["mjd"])
    return urls, mjds


def probe_frames(urls, mjds, coord, progress=None, **_):
    out = []
    for i, url in enumerate(urls, start=1):
        f = _render(url)
        out.append({k: f[k] for k in ("mjd", "url", "wavelength", "bandwidth", "flux",
                                      "flux_err", "flux_unit")})
        if progress:
            progress(i, len(urls))
    return out, 0


def download_full(chosen, coord, progress=None, **_):
    frames = []
    for i, c in enumerate(chosen, start=1):
        frames.append(dict(_render(c["url"])))
        if progress:
            progress(i, len(chosen))
    return sorted(frames, key=lambda f: f["mjd"]), 0

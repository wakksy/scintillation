"""
tests/smoke.py -- offline test of Scintillation, using the FAKE demo data (no network).

AI use (NASA Space Apps SI disclosure): this file was written with help from
Claude (Anthropic), used through Claude Code, and reviewed and tested by the
Scintillation team. The idea and design decisions are the team's. See README.md.

Run from the project folder (inside the Python 3.12 venv):
    python -I tests/smoke.py .

It checks:
  1. measure_target gets back the brightness of a fake star it is given
  2. the demo spectrum shows the invented dips at the right wavelengths
  3. the whole app runs in demo mode, in both modes, with every tab (incl. Spectrum)
  4. quick-target proper motion (stored positions only, so no SIMBAD lookup)
Prints PASS/FAIL for each and exits with code 1 if anything failed.
"""

import os
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np                                 # noqa: E402
import astropy.units as u                          # noqa: E402
from astropy.coordinates import SkyCoord           # noqa: E402

import demo_data                                   # noqa: E402
import spherex_core as core                        # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# 1. Photometry on a synthetic star -------------------------------------------------
centre = SkyCoord(150 * u.deg, 2 * u.deg)
wcs, (n, _) = core.make_target_wcs(centre, 0.02)
img = np.random.default_rng(0).normal(0, 1, (n, n)) + 5
x, y = wcs.world_to_pixel(centre)
demo_data._add_source(img, float(x), float(y), 100.0)
flux, err = core.measure_target(img, wcs, centre)
# a 3x3 box holds ~89% of a sigma=0.8 px Gaussian
check("measure_target recovers a fake star", 80 < flux < 100, f"flux={flux:.1f}±{err:.1f} (expect ~89)")

# 2. Demo spectrum has its dips --------------------------------------------------
coord = SkyCoord(133.760098 * u.deg, -7.242266 * u.deg)
urls, mjds = demo_data.find_images(coord, 0.145, "D2")
probes, _ = demo_data.probe_frames(urls, mjds, coord)
pts = core.spectrum_points(probes)
wl = np.array([p["wavelength"] for p in pts])
fl = np.array([p["flux"] for p in pts])
in_dip = np.median(fl[np.abs(wl - 1.40) < 0.05])
outside = np.median(fl[(wl > 1.15) & (wl < 1.30)])
check("demo spectrum shows the 1.4 um dip", in_dip < 0.8 * outside,
      f"dip {in_dip:.1f} vs continuum {outside:.1f}, {len(pts)} points")
check("spectrum binning (17 per detector)", 5 <= len(core.bin_spectrum(pts, "D2")) <= 17)

# 2b. Colour picture: the demo mover's 1.4 um dip sits in D2's green third -----------
from PIL import Image  # noqa: E402,F401

frames, _ = demo_data.download_full([{"url": x, "mjd": m} for x, m in zip(urls, mjds)], coord)
wcs2, shape2 = core.make_target_wcs(coord, 0.10)
aligned_all = core.align_frames(frames, wcs2, shape2)
first_epoch = core.group_epochs(aligned_all, 45)[0]
img, summary = core.colour_image(first_epoch, "D2", 300)
a = np.asarray(img).astype(float)
c = a.shape[0] // 2
patch = a[c - 12:c + 12, c - 12:c + 12].reshape(-1, 3)
mover = patch[patch.sum(1).argmax()]
check("colour picture: mover is green-poor (magenta)", img is not None and
      mover[1] < 0.85 * (mover[0] + mover[2]) / 2,
      f"mover RGB {mover.astype(int)}, frames per colour {[s['frames'] for s in summary]}")

# 2c. Auto-retry when IRSA says busy ---------------------------------------------
real_once, real_waits = core._find_images_once, core.TAP_RETRY_WAITS_S
calls, retries = {"n": 0}, []


def flaky(*args):
    calls["n"] += 1
    if calls["n"] < 3:
        raise RuntimeError("502 Server Error: Proxy Error for url: https://irsa...")
    return "TABLE"


core._find_images_once, core.TAP_RETRY_WAITS_S = flaky, (0, 0)
try:
    got = core.find_images(coord, 0.1, "D2", on_retry=lambda k, e: retries.append(k))
    check("auto-retry: succeeds on the 3rd try after two 502s",
          got == "TABLE" and retries == [2, 3], f"calls={calls['n']}, retries={retries}")

    def broken(*args):
        calls["n"] += 1
        raise ValueError("bad ADQL")
    calls["n"] = 0
    core._find_images_once = broken
    try:
        core.find_images(coord, 0.1, "D2")
        check("auto-retry: real errors are not retried", False, "no error raised")
    except ValueError:
        check("auto-retry: real errors are not retried", calls["n"] == 1, f"calls={calls['n']}")
finally:
    core._find_images_once, core.TAP_RETRY_WAITS_S = real_once, real_waits

# 3. The app, end to end, in demo mode ------------------------------------------------
from streamlit.testing.v1 import AppTest  # noqa: E402

for mode in ("Matched-wavelength frames", "Epoch stacks"):
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=180)
    at.run()
    at.radio(key="source").set_value("Demo: generated fake data")
    at.radio(key="mode").set_value(mode)
    [b for b in at.button if "Fetch" in b.label][0].click()
    at.run()
    ok = not at.exception
    tabs = [t.label for t in at.tabs]
    charts = len(at.get("vega_lite_chart"))   # the spectrum chart
    demo_banner = any("DEMO MODE" in m.value for m in at.markdown)
    colour_tab = [t for t in at.tabs if t.label == "Colour"]
    colour_ok = bool(colour_tab) and (
        len(colour_tab[0].get("image")) >= 1 if mode == "Epoch stacks"
        else any("Epoch stacks" in i.value for i in colour_tab[0].info))
    check(f"app runs in demo mode ({mode})",
          ok and "Spectrum" in tabs and demo_banner and charts >= 1 and colour_ok,
          f"exception={at.exception if not ok else None}, tabs={tabs}, charts={charts}, "
          f"errors={[e.value[:60] for e in at.error]}, "
          f"warnings={[w.value[:60] for w in at.warning if 'DEMO' not in w.value]}")

# 4. Proper motion of quick targets (stored positions, no network) ------------------
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
import ast  # noqa: E402
from astropy.time import Time  # noqa: E402

tree = ast.parse(src)
keep = [n for n in tree.body
        if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None)
            in ("DISPLAY_EPOCH", "PRESETS"))
        or (isinstance(n, ast.FunctionDef) and n.name == "preset_position")]
ns = {"SkyCoord": SkyCoord, "u": u, "Time": Time}
exec(compile(ast.Module(body=keep, type_ignores=[]), "app_presets", "exec"), ns)
wise = ns["preset_position"](ns["PRESETS"]["WISE 0855"])
sep = SkyCoord(wise[0] * u.deg, wise[1] * u.deg).separation(
    SkyCoord(133.760098 * u.deg, -7.242266 * u.deg)).arcsec
check("WISE 0855 moved to 2026.0", sep < 0.1, f"{sep:.3f} arcsec from expected")
barn = {k: v for k, v in ns["PRESETS"]["Barnard's Star"].items() if k != "lookup"}
moved = SkyCoord(*ns["preset_position"](barn)[:2], unit="deg").separation(
    SkyCoord(*barn["coord"], unit="deg")).arcsec
check("Barnard's Star moved ~270 arcsec", 265 < moved < 275, f"{moved:.1f} arcsec")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

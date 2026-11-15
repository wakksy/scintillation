"""
irsa_health.py -- is IRSA slow, or is it my app?

Times each step the app performs, one at a time, each with a time limit:
  1. a trivial search of IRSA's SPHEREx tables
  2. the real search for WISE 0855 (the same one the app does)
  3. downloading ONE tiny cutout (what the wavelength probes do)
  4. downloading ONE full-size cutout (what the blink frames need)

Run it with:   python irsa_health.py        (takes at most about 4 minutes)

Reading the result:
  * a step that is slow or times out  -> that part of IRSA is struggling
  * every step quick                  -> IRSA is fine; the problem is in the app
    (paste this script's output to your mentor either way)

AI use (NASA Space Apps SI disclosure): this file was written with help from
Claude (Anthropic), used through Claude Code, and reviewed and tested by the
Scintillation team. The idea and design decisions are the team's. See README.md.
"""

import time

import astropy.units as u
from astropy.coordinates import SkyCoord

import spherex_core as core

COORD = SkyCoord(ra=133.795129 * u.deg, dec=-7.245147 * u.deg, frame="icrs")  # WISE 0855
DETECTOR = "D2"
FOV = 0.10


def timed(label, fn, timeout_s):
    print(f"{label} ...", flush=True)
    t0 = time.time()
    try:
        value = core.call_with_timeout(fn, timeout_s=timeout_s)
    except TimeoutError:
        print(f"   -> NO ANSWER after {timeout_s} s: IRSA is not responding at this step.\n")
        return None
    except Exception as err:
        print(f"   -> ERROR after {time.time() - t0:.1f} s: {err}\n")
        return None
    seconds = time.time() - t0
    verdict = "fast" if seconds < 15 else ("slow" if seconds < 45 else "very slow")
    print(f"   -> OK in {seconds:.1f} s ({verdict})\n")
    return value


def main():
    print("IRSA health check\n")

    timed("1. Trivial search (SELECT TOP 1)",
          lambda: core.Irsa.query_tap("SELECT TOP 1 uri FROM spherex.artifact").to_table(), 60)

    table = timed("2. Real search for WISE 0855 (all images covering it)",
                  lambda: core.find_images(COORD, FOV * core.CUTOUT_FACTOR, DETECTOR, 90), 100)
    if table is None or len(table["uri"]) == 0:
        print("Cannot test downloads without search results. Fix step 2 first.")
        return
    print(f"   (the search found {len(table['uri'])} images)\n")

    url = str(table["uri"][0])
    timed("3. ONE tiny cutout (wavelength probe)",
          lambda: core._download_cutout(core.with_size(url, core.PROBE_SIZE_DEG), COORD), 70)
    timed("4. ONE full-size cutout",
          lambda: core._download_cutout(url, COORD), 100)

    print("Done. If steps 1-2 were slow, the search service is the bottleneck;\n"
          "if 3-4 were slow, the cutout service is. Both are on IRSA's side.")


if __name__ == "__main__":
    main()

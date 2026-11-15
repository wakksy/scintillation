"""
app.py -- SPHEREx Blink: a public web tool to see how a patch of sky changes over time.

Run locally with:   python -m streamlit run app.py

Needs spherex_core.py in the same folder.
URL parameters work, so a link like  ...?ra=133.795&dec=-7.245&fov=0.1  opens the
tool already pointed at that spot (handy for embedding in other pages).

Two ways to build the animation:
  * Matched-wavelength frames: a few single frames, all at about the same colour
  * Epoch stacks: every frame of one observing period is added together, giving one
    deeper picture per epoch (the brief's "add together the data of each detector")
Both can be blinked, stepped through, and compared (difference / sum).
"""

import time

import astropy.units as u
import numpy as np
import pandas as pd
import streamlit as st
from astropy.coordinates import SkyCoord
from astropy.time import Time

import spherex_core as core

# Field-of-view limits (degrees). 0.20 deg = 12 arcmin: wide enough to follow
# faster movers, small enough that downloads stay quick and reliable.
FOV_MIN, FOV_MAX, FOV_DEFAULT = 0.02, 0.20, 0.10
MAX_CANDIDATES = 80      # safety cap on how many images are checked per fetch
MAX_STACK_FRAMES = 60    # safety cap on how many frames an epoch-stack fetch downloads

MATCH_MODE = "Matched-wavelength frames"
EPOCH_MODE = "Epoch stacks"

PRESETS = {  # name -> (ra, dec) in degrees, or a name to look up
    "WISE 0855": (133.795129, -7.245147),  # coordinates from the challenge brief
    "Barnard's Star": "Barnard's star",
    "Epsilon Indi": "eps Ind",
    "RW Aurigae": "RW Aur",
    "R Draconis": "R Dra",
}

st.set_page_config(page_title="SPHEREx Blink", page_icon="🔭", layout="wide")


# ---- State and helpers -------------------------------------------------------
def init_state():
    qp = st.query_params
    st.session_state.setdefault("ra_text", qp.get("ra", "133.795129"))
    st.session_state.setdefault("dec_text", qp.get("dec", "-7.245147"))
    try:
        fov0 = float(qp.get("fov", FOV_DEFAULT))
    except ValueError:
        fov0 = FOV_DEFAULT
    st.session_state.setdefault("fov", round(min(max(fov0, FOV_MIN), FOV_MAX), 2))
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("notice", None)


def apply_preset():
    """Fill the RA/Dec boxes when a quick target is chosen."""
    name = st.session_state.get("preset", "(custom)")
    st.session_state["preset_error"] = None
    if name not in PRESETS:
        return
    target = PRESETS[name]
    try:
        if isinstance(target, str):
            coord = SkyCoord.from_name(target)
            ra, dec = coord.ra.deg, coord.dec.deg
        else:
            ra, dec = target
        st.session_state["ra_text"] = f"{ra:.6f}"
        st.session_state["dec_text"] = f"{dec:.6f}"
    except Exception as err:
        st.session_state["preset_error"] = f"Could not look up {name}: {err}"


@st.cache_data(ttl=900, show_spinner=False)
def cached_find(ra_deg, dec_deg, cutout_deg, detector):
    """Ask IRSA which images cover the position (remembered for 15 minutes)."""
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    table = core.find_images(coord, cutout_deg, detector)
    urls = [str(x) for x in table["uri"]]
    mjds = [float(x) for x in table["time_bounds_lower"]]
    return urls, mjds


def fail(status, label, kind, message, result=None):
    """Show a problem to the user (kind is 'error' or 'warning')."""
    status.update(label=label, state="error")
    st.session_state["notice"] = (kind, message)
    if result is not None:
        st.session_state["result"] = result


def thin_evenly(urls, mjds, cap):
    if len(urls) <= cap:
        return urls, mjds
    idx = np.linspace(0, len(urls) - 1, cap).astype(int)
    return [urls[i] for i in idx], [mjds[i] for i in idx]


def make_progress(text):
    """A progress bar whose label also shows how many seconds have passed."""
    bar = st.progress(0.0, text=f"{text}...")
    t0 = time.time()

    def update(k, n):
        bar.progress(k / n, text=f"{text}: {k} of {n}  ({time.time() - t0:.0f} s)")

    return bar, update


def fetch_matched(status, p, coord, urls, mjds, n_found):
    """Matched mode: choose a few well-spaced frames, download only those."""
    tol = None if p["wl_mode"] == "Off" else p["tol"]
    n_probe_failed = 0

    # Step 1: decide WHICH frames to use, downloading as little as possible.
    if tol is None:
        # Wavelength ignored: the dates alone decide, so nothing needs downloading yet.
        candidates = [{"mjd": m, "url": x, "wavelength": None} for x, m in zip(urls, mjds)]
    else:
        # Read each image's wavelength from a tiny, quick cutout (cached forever).
        bar, on_progress = make_progress("Checking wavelengths")
        candidates, n_probe_failed = core.probe_frames(urls, mjds, coord, progress=on_progress)
        bar.empty()
        if not candidates:
            fail(status, "IRSA did not deliver", "error",
                 "IRSA's server was too busy or too slow: none of the requests finished "
                 f"within {core.PROBE_DEADLINE_S} seconds. Wait a few minutes and press Fetch "
                 "again (anything already downloaded is kept). If it keeps happening, "
                 "run irsa_health.py to see which step is slow.")
            return None

    chosen, wl_used = core.choose_frames(candidates, None, tol, p["how_many"], p["gap"])
    result = {"mode": MATCH_MODE, "candidates": candidates, "frames": [], "aligned": [],
              "chosen_urls": {f["url"] for f in chosen}, "wl_used": wl_used,
              "n_found": n_found, "n_failed": n_probe_failed, "p": p}

    if len(chosen) < 2:
        fail(status, "Too few frames match", "warning",
             f"Only {len(chosen)} frame(s) satisfy the settings. Lower the minimum days apart, "
             "widen the wavelength tolerance, turn wavelength matching off, or try 'Epoch "
             "stacks' mode. (The Frame list tab shows what was found.)", result)
        return None

    # Step 2: download full-size cutouts for the chosen frames only.
    bar, on_progress = make_progress("Downloading frames")
    frames, n_failed = core.download_full(chosen, coord, progress=on_progress)
    bar.empty()
    result["frames"], result["n_failed"] = frames, n_probe_failed + n_failed

    if len(frames) < 2:
        fail(status, "Downloads failed", "error",
             "Too few frames arrived in time (IRSA is busy or slow). Wait a minute and press "
             "Fetch again; frames already downloaded are kept.", result)
        return None

    target_wcs, shape = core.make_target_wcs(coord, p["fov"])
    result["aligned"] = core.align_frames(frames, target_wcs, shape)
    status.update(label=f"Ready: {len(frames)} frames", state="complete", expanded=False)
    return result


def fetch_epoch_stacks(status, p, coord, urls, mjds, n_found):
    """Epoch mode: download every frame, group them into epochs, add each epoch together."""
    urls, mjds = thin_evenly(urls, mjds, MAX_STACK_FRAMES)
    candidates = [{"mjd": m, "url": x, "wavelength": None} for x, m in zip(urls, mjds)]

    bar, on_progress = make_progress("Downloading every frame (epoch stacks need them all)")
    frames, n_failed = core.download_full(candidates, coord, progress=on_progress)
    bar.empty()

    if len(frames) < 2:
        fail(status, "Downloads failed", "error",
             "Too few frames arrived in time (IRSA is busy or slow). Wait a minute and press "
             "Fetch again; frames already downloaded are kept, so each try gets further.")
        return None

    target_wcs, shape = core.make_target_wcs(coord, p["fov"])
    aligned_frames = core.align_frames(frames, target_wcs, shape)
    stacks = core.stack_epochs(aligned_frames, p["epoch_gap"], p["combine"])

    epoch_of = {x: s["epoch"] for s in stacks for x in s["urls"]}
    for f in frames:
        f["epoch"] = epoch_of.get(f["url"])
    result = {"mode": EPOCH_MODE, "candidates": frames, "frames": frames, "aligned": stacks,
              "chosen_urls": {f["url"] for f in frames}, "wl_used": None,
              "n_found": n_found, "n_failed": n_failed, "p": p}

    if len(stacks) < 2:
        fail(status, "Only one epoch", "warning",
             "All frames fall into a single epoch. Lower the 'epoch gap' setting, or use a "
             "position that SPHEREx has visited in more than one survey.", result)
        return None

    status.update(label=f"Ready: {len(stacks)} epochs from {len(frames)} frames",
                  state="complete", expanded=False)
    return result


def run_fetch(p):
    """Query IRSA, download cutouts, build the frames. Stores the result for display."""
    st.session_state["result"] = None
    st.session_state["notice"] = None

    try:
        coord = core.parse_coord(p["ra_text"], p["dec_text"])
    except ValueError as err:
        st.session_state["notice"] = ("error", str(err))
        return

    cutout = round(p["fov"] * core.CUTOUT_FACTOR, 4)

    with st.status("Fetching SPHEREx frames...", expanded=True) as status:
        try:
            st.write(f"Asking IRSA which images cover this position "
                     f"(usually ~20 s, gives up after {core.TAP_TIMEOUT_S} s)...")
            urls, mjds = cached_find(round(coord.ra.deg, 6), round(coord.dec.deg, 6),
                                     cutout, p["detector"])
        except TimeoutError:
            fail(status, "IRSA search timed out", "error",
                 f"IRSA's search service did not answer within {core.TAP_TIMEOUT_S} seconds. "
                 "It may be overloaded, so wait a few minutes and press Fetch again. "
                 "(This is IRSA being slow, not a fault in this app.)")
            return
        except Exception as err:
            fail(status, "Could not reach IRSA", "error",
                 f"IRSA did not answer ({err}). Wait a moment and press Fetch again.")
            return

        n_found = len(urls)
        releases = sorted({core.release_of(x) for x in urls})
        if p["release"] != "All releases":
            keep = [i for i, x in enumerate(urls) if core.release_of(x) == p["release"]]
            if not keep and urls:
                fail(status, "No images in that release", "warning",
                     f"No '{p['release']}' images at this position yet "
                     f"(found releases: {', '.join(releases)}). Try 'All releases'.")
                return
            urls, mjds = [urls[i] for i in keep], [mjds[i] for i in keep]

        if not urls:
            fail(status, "No images found", "warning",
                 f"SPHEREx has no {p['detector']} images covering this position (yet). "
                 "Try another detector or position.")
            return

        urls, mjds = thin_evenly(urls, mjds, MAX_CANDIDATES)

        if p["mode"] == EPOCH_MODE:
            result = fetch_epoch_stacks(status, p, coord, urls, mjds, n_found)
        else:
            result = fetch_matched(status, p, coord, urls, mjds, n_found)
        if result is None:
            return

    st.session_state["result"] = result
    # Put the position in the URL so the view can be shared or embedded.
    st.query_params["ra"] = f"{coord.ra.deg:.6f}"
    st.query_params["dec"] = f"{coord.dec.deg:.6f}"
    st.query_params["fov"] = f"{p['fov']:.2f}"


def describe(f):
    """One-line description of a displayed frame (single frame or epoch stack)."""
    if f.get("label"):
        return f["label"]
    return f"{Time(f['mjd'], format='mjd').iso[:10]} | {f['wavelength']:.2f} um"


# ---- Page --------------------------------------------------------------------
init_state()

st.title("🔭 SPHEREx Blink")
st.write("Enter a patch of sky and watch how it changes between SPHEREx observations. "
         "Anything that moves against the fixed stars (asteroids, comets, nearby "
         "brown dwarfs) will appear to jump from frame to frame.")

with st.sidebar:
    st.header("Where to look")
    st.selectbox("Quick targets", ["(custom)", *PRESETS], key="preset", on_change=apply_preset)
    if st.session_state.get("preset_error"):
        st.warning(st.session_state["preset_error"])
    if st.session_state.get("preset") in ("Barnard's Star", "Epsilon Indi"):
        st.caption("Name lookups give the catalogue (year 2000) position. Fast-moving "
                   "stars have moved since, so they may sit off-centre.")

    with st.form("controls"):
        st.text_input("Right ascension (RA)", key="ra_text",
                      help="Degrees (133.795) or hours (08h55m10.83s)")
        st.text_input("Declination (Dec)", key="dec_text",
                      help="Degrees (-7.245) or degrees-minutes-seconds (-07d14m42.5s)")
        st.slider("Field of view (degrees)", FOV_MIN, FOV_MAX, step=0.01, key="fov",
                  help="Width of the area shown. Larger areas download more data.")
        st.caption(f"Range {FOV_MIN}-{FOV_MAX} deg (0.1 deg = 6 arcmin).")

        st.subheader("Which data")
        detector = st.selectbox("Detector", core.DETECTORS, index=1,
                                help="D1 0.75-1.09 um, D2 1.10-1.62, D3 1.63-2.41, "
                                     "D4 2.42-3.82, D5 3.83-4.41, D6 4.42-5.00. "
                                     "Very red objects (cold brown dwarfs) are faint in the "
                                     "bluer detectors.")
        release = st.selectbox("Data release", ["All releases", "dr1", "qr3"])

        st.subheader("How to build the animation")
        mode = st.radio("Mode", [MATCH_MODE, EPOCH_MODE], key="mode",
                        help="Matched: a few single frames at about the same colour. "
                             "Epoch stacks: all frames of each observing period added "
                             "together (deeper, like-for-like pictures).")

        st.caption("**Matched-wavelength mode settings**")
        gap = st.slider("Minimum days between frames", 0, 60, 10)
        wl_mode = st.selectbox("Wavelength matching", ["Auto (most frames)", "Off"],
                               help="Off lets frames of different colours mix. That can "
                                    "make stars flicker and red objects appear or vanish.")
        tol = st.slider("Wavelength tolerance (um)", 0.02, 0.50, 0.10, step=0.01)
        how_many = st.slider("Maximum frames", 2, 16, 8)

        st.caption("**Epoch-stack mode settings**")
        epoch_gap = st.slider("Epoch gap (days)", 5, 120, 45,
                              help="Frames closer together than this belong to the same "
                                   "epoch. SPHEREx revisits the sky about every six months.")
        combine = st.radio("Combine frames by", ["Mean", "Median"], horizontal=True,
                           help="Mean keeps faint movers; median rejects things that move "
                                "within an epoch.")

        submitted = st.form_submit_button("Fetch frames", type="primary")

# Open straight to a result when the page is loaded from a link with ra/dec in it.
autoload = False
if "autoloaded" not in st.session_state:
    st.session_state["autoloaded"] = True
    autoload = "ra" in st.query_params and "dec" in st.query_params

if submitted or autoload:
    run_fetch({"ra_text": st.session_state["ra_text"], "dec_text": st.session_state["dec_text"],
               "fov": round(float(st.session_state["fov"]), 3), "detector": detector,
               "release": release, "mode": mode, "gap": gap, "wl_mode": wl_mode, "tol": tol,
               "how_many": how_many, "epoch_gap": epoch_gap, "combine": combine.lower()})

notice = st.session_state.get("notice")
if notice:
    (st.error if notice[0] == "error" else st.warning)(notice[1])

result = st.session_state.get("result")
if result is None:
    if not notice:
        st.info("Choose a position in the sidebar and press **Fetch frames**. "
                "The first fetch takes a minute; downloaded frames are remembered.")
    st.stop()

aligned = result["aligned"]
p = result["p"]
is_epoch = result["mode"] == EPOCH_MODE

# ---- Summary line and warnings -------------------------------------------------
if is_epoch:
    st.caption(f"{p['detector']}  |  {len(aligned)} epochs built from "
               f"{len(result['frames'])} frames ({p['combine']} stack, epoch gap "
               f"{p['epoch_gap']} days)  |  IRSA listed {result['n_found']} images"
               + (f", {result['n_failed']} failed or timed out (press Fetch again to retry)"
                  if result["n_failed"] else ""))
else:
    wl_text = (f"around {result['wl_used']:.2f} um" if result["wl_used"] is not None
               else "any wavelength")
    st.caption(f"{p['detector']}  |  {len(aligned)} frames shown ({wl_text}, at least "
               f"{p['gap']} days apart)  |  IRSA listed {result['n_found']} images, "
               f"{len(result['candidates'])} checked, {len(result['frames'])} downloaded"
               + (f", {result['n_failed']} failed or timed out (press Fetch again to retry)"
                  if result["n_failed"] else ""))

if not is_epoch and p["wl_mode"] == "Off":
    st.warning("**Wavelength matching is off.** These frames are different colours, so a "
               "star can change brightness from frame to frame and a very red object can "
               "appear or vanish for that reason alone. Treat flicker as a possible colour "
               "effect, not necessarily motion. Turn matching on (or use Epoch stacks) for "
               "a like-for-like comparison.")

if is_epoch and aligned:
    st.info("**Epoch stacks:** each picture adds together every frame of one observing "
            "period, so it is deeper and covers the whole detector band. Compare epochs "
            "whose wavelength coverage and frame counts are similar (see the Frame list tab).")
    counts = [s["n"] for s in aligned]
    thin = [s["epoch"] for s in aligned if s["n"] < 0.5 * max(counts)]
    if thin:
        st.warning(f"Epoch(s) {', '.join(map(str, thin))} contain far fewer frames than the "
                   "others, so their colour coverage and depth differ. Differences between "
                   "them may not be real changes.")


def frame_list_table():
    measured = {f["url"]: f["wavelength"] for f in result["frames"]}
    rows = []
    for c in sorted(result["candidates"], key=lambda c: c["mjd"]):
        wl = c["wavelength"] if c["wavelength"] is not None else measured.get(c["url"])
        row = {"Date": Time(c["mjd"], format="mjd").iso[:10], "MJD": round(c["mjd"], 1),
               "Wavelength (um)": None if wl is None else round(wl, 3)}
        if is_epoch:
            row["Epoch"] = c.get("epoch")
        else:
            row["Used in blink"] = c["url"] in result["chosen_urls"]
        rows.append(row)
    return pd.DataFrame(rows)


def epoch_table():
    return pd.DataFrame([{"Epoch": s["epoch"], "Dates": s["dates"], "Frames": s["n"],
                          "Wavelength range (um)": f"{s['wl_min']:.2f} - {s['wl_max']:.2f}"}
                         for s in aligned])


# ---- Tabs --------------------------------------------------------------------
if aligned:
    tab_auto, tab_manual, tab_compare, tab_table = st.tabs(
        ["Auto blink", "Manual", "Compare (add / subtract)", "Frame list"])

    with tab_auto:
        ms = st.slider("Time per frame (milliseconds)", 100, 2000, 500, step=50)
        st.image(core.make_gif_bytes(aligned, ms))
        st.caption("Looping animation, oldest first. North is up, east is left. "
                   "Labels show the observation date and wavelength (or the epoch).")

    with tab_manual:
        idx = st.slider("Frame", 1, len(aligned), 1) if len(aligned) > 1 else 1
        f = aligned[idx - 1]
        st.image(core.frame_image(f))
        st.caption(f"{idx} of {len(aligned)}  |  {describe(f)}")

    with tab_compare:
        if len(aligned) < 2:
            st.info("At least two frames (or epochs) are needed to compare.")
        else:
            names = [describe(f) for f in aligned]
            col_a, col_b, col_op = st.columns(3)
            ia = col_a.selectbox("Image A (earlier)", range(len(aligned)),
                                 format_func=lambda i: names[i], index=0)
            ib = col_b.selectbox("Image B (later)", range(len(aligned)),
                                 format_func=lambda i: names[i], index=len(aligned) - 1)
            op_label = col_op.selectbox("Operation", ["Difference (B - A)", "Sum (A + B)"])
            op = "difference" if op_label.startswith("Difference") else "sum"
            sigma = 5.0
            if op == "difference":
                sigma = st.slider("Stretch (noise levels)", 2.0, 12.0, 5.0, step=0.5,
                                  help="Lower values show fainter changes but more noise.")
            if ia == ib:
                st.warning("Pick two different images.")
            else:
                st.image(core.combine_image(aligned[ia]["aligned"], aligned[ib]["aligned"],
                                            op, sigma))
                if op == "difference":
                    st.caption("**Red** = brighter in B, **blue** = fainter in B, white = no "
                               "change, grey = no data. Something that moved shows up as a "
                               "red spot next to a blue spot. Each image has its background "
                               "level removed first. For a fair comparison, A and B should "
                               "have similar wavelength coverage.")
                else:
                    st.caption("A + B, each with its background level removed. Adding images "
                               "raises the signal-to-noise of faint sources.")

    with tab_table:
        if is_epoch:
            st.subheader("Epochs")
            st.dataframe(epoch_table(), hide_index=True)
            st.subheader("All downloaded frames")
        st.dataframe(frame_list_table(), hide_index=True)

elif result["candidates"]:
    # Too few frames matched: still show what was found, so the settings can be tuned.
    st.subheader("Frames found")
    st.dataframe(frame_list_table(), hide_index=True)

st.divider()
st.caption("Data: NASA SPHEREx via the NASA/IPAC Infrared Science Archive (IRSA). "
           "Method adapted from the IRSA SPHEREx cutouts tutorial. Pictures are "
           "resampled for display and are not suitable for measuring brightness. "
           "Moving objects shown here are candidates for review, not confirmed discoveries.")

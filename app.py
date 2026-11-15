"""
app.py -- Scintillation: a public web tool to see how a patch of sky changes over time
in NASA SPHEREx images, with optional Gaia DR3 and CatWISE2020 overlays.

AI use (NASA Space Apps SI disclosure): this file was written with help from
Claude (Anthropic), used through Claude Code, and reviewed and tested by the
Scintillation team. The idea and design decisions are the team's. See README.md.

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

import base64
import time
from pathlib import Path
from urllib.parse import quote

import astropy.units as u
import numpy as np
import pandas as pd
import streamlit as st
from astropy.coordinates import SkyCoord
from astropy.time import Time

import demo_data
import spherex_core as core

# Field-of-view limits (degrees). 0.20 deg = 12 arcmin: wide enough to follow
# faster movers, small enough that downloads stay quick and reliable.
FOV_MIN, FOV_MAX, FOV_DEFAULT = 0.02, 0.20, 0.10
MAX_CANDIDATES = 80      # safety cap on how many images are checked per fetch
MAX_STACK_FRAMES = 60    # safety cap on how many frames an epoch-stack fetch downloads

MATCH_MODE = "Matched-wavelength frames"
EPOCH_MODE = "Epoch stacks"

# Fast-moving stars are moved forward to this date, so they sit in the centre of the
# view in SPHEREx data (taken 2025-2026) instead of at an old catalogue position.
DISPLAY_EPOCH = 2026.0

# Quick targets. Keys per target:
#   lookup   a name for SIMBAD (optional; positions from SIMBAD are for epoch J2000)
#   coord    (ra, dec) degrees, used if there is no lookup or the lookup fails
#   epoch    the year that "coord" (and the SIMBAD position) refers to
#   pm       proper motion (pmra*cos(dec), pmdec) in mas/yr; None = slow, leave as is
#   dist_pc  distance in parsecs (needed by astropy to move the star)
PRESETS = {
    "WISE 0855": {
        # Brief's coordinates come from 2010 WISE data. PM: Kirkpatrick et al. 2021.
        "coord": (133.795129, -7.245147), "epoch": 2010.6,
        "pm": (-8123.7, 673.2), "dist_pc": 2.28,
    },
    "Barnard's Star": {
        # PM: Gaia DR3. Fallback coord is the SIMBAD J2000 position.
        "lookup": "Barnard's star", "coord": (269.452083, 4.693364), "epoch": 2000.0,
        "pm": (-801.551, 10362.394), "dist_pc": 1.83,
    },
    "Epsilon Indi B": {
        # "eps Ind" alone is star A, ~400" away. Fallback coord: NASA Exoplanet Archive.
        # PM: B's own Gaia DR3 value (source 6412596012146801152).
        "lookup": "eps Ind B", "coord": (331.043833, -56.782708), "epoch": 2000.0,
        "pm": (3982.0, -2466.8), "dist_pc": 3.7,
    },
    "RW Aurigae": {"lookup": "RW Aur", "pm": None},
    "R Draconis": {"lookup": "R Dra", "pm": None},
}


def preset_position(target):
    """Return (ra, dec, note) for a quick target, moved forward to DISPLAY_EPOCH."""
    note = None
    coord = None
    if target.get("lookup"):
        try:
            coord = SkyCoord.from_name(target["lookup"])
            coord = SkyCoord(ra=coord.ra, dec=coord.dec, frame="icrs")
        except Exception as err:
            if "coord" not in target:
                raise
            note = "Name lookup unavailable right now; using a stored position instead."
    if coord is None:
        ra, dec = target["coord"]
        coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")

    if target.get("pm"):
        pmra, pmdec = target["pm"]
        moving = SkyCoord(ra=coord.ra, dec=coord.dec, frame="icrs",
                          distance=target["dist_pc"] * u.pc,
                          pm_ra_cosdec=pmra * u.mas / u.yr, pm_dec=pmdec * u.mas / u.yr,
                          radial_velocity=0 * u.km / u.s,
                          obstime=Time(target.get("epoch", 2000.0), format="jyear"))
        coord = moving.apply_space_motion(new_obstime=Time(DISPLAY_EPOCH, format="jyear"))
    return coord.ra.deg, coord.dec.deg, note

APP_NAME = "Scintillation"
CAT_GAIA = "Gaia DR3 (visible light)"
CAT_WISE = "CatWISE2020 (WISE infrared)"
SOURCE_REAL = "Real SPHEREx data (IRSA)"
SOURCE_DEMO = "Demo: generated fake data"


def engine(p):
    """Where frames come from: the real IRSA engine, or the fake demo generator.
    Both offer find_images / probe_frames / download_full."""
    return demo_data if p.get("source") == SOURCE_DEMO else core

ASSETS = Path(__file__).parent / "assets"
st.set_page_config(page_title=APP_NAME, page_icon=str(ASSETS / "favicon.png"), layout="wide")


@st.cache_data(show_spinner=False)
def logo_data_uri():
    """The logo as an inline image (so no extra file request is needed)."""
    return "data:image/png;base64," + base64.b64encode(
        (ASSETS / "logo.png").read_bytes()).decode()


def wordmark(extra=""):
    """Logo + "Scintillation" in the header, optionally followed by extra HTML."""
    return (f'<div class="sc-mast"><span class="sc-word"><img class="sc-logo" '
            f'src="{logo_data_uri()}" alt="">Scintillation</span>{extra}</div>')

# Colour scheme (also in .streamlit/config.toml). The four team colours plus two
# quiet supporting tones: a darker "darkroom" behind pictures so they glow, and a
# pale sky blue for secondary text. Yellow is kept for things to act on or notice.
NAVY, INDIGO, STEEL, YELLOW = "#030164", "#363199", "#2D7495", "#E8E085"
DARKROOM, SKY, TEXT = "#01003A", "#C9E4F2", "#F3F4FB"
BG_TOP, BG_BOTTOM = "#070979", "#02013E"   # background gradient ends
LIGHT_BLUE = "#6FB3D9"


def inject_css():
    """Fonts, layout and the few styles the theme file cannot set."""
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@700&family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap');

html, body, .stApp, [data-testid="stSidebar"] {{
  font-family: 'Atkinson Hyperlegible', 'Segoe UI', sans-serif; }}
h1, h2, h3, .stTabs [data-baseweb="tab"] {{
  font-family: 'Bricolage Grotesque', 'Segoe UI', sans-serif !important; }}
.block-container {{ max-width: 1560px; padding: 1.4rem 2rem 3rem 2rem; }}
/* Page background: a slightly brighter, bluer navy at the top that fades to deep
   navy at the bottom. The top bar is transparent so the gradient runs behind it. */
.stApp {{ background: linear-gradient(180deg, {BG_TOP} 0%, {NAVY} 45%, {BG_BOTTOM} 100%)
  fixed; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* Masthead: the wordmark and where we are looking. */
.sc-mast {{ display: flex; flex-wrap: wrap; align-items: center; gap: .4rem 1.2rem;
  margin-bottom: 1.1rem; }}
/* Wordmark: the logo plus "Scintillation" in Chakra Petch, whose cut corners echo the
   logo's rays, filled with the logo's own yellow-to-orange gradient. */
.sc-word {{ display: inline-flex; align-items: center; gap: .6rem; }}
.sc-word, .sc-word * {{ font-family: 'Chakra Petch', 'Bricolage Grotesque', sans-serif !important; }}
.sc-word {{ font-weight: 700; font-size: 2.2rem; letter-spacing: .02em; line-height: 1;
  background: linear-gradient(100deg, #FFE600 0%, #FFC400 45%, #FFA000 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  filter: drop-shadow(0 0 10px #FFC40044); }}
.sc-logo {{ width: 2.6rem; height: 2.6rem; flex: none;
  animation: sc-twinkle 4s ease-in-out infinite; }}
@keyframes sc-twinkle {{ 0%, 100% {{ transform: scale(1); filter: brightness(1); }}
  50% {{ transform: scale(.92) rotate(12deg); filter: brightness(1.25); }} }}
@media (prefers-reduced-motion: reduce) {{ .sc-logo {{ animation: none; }} }}
.sc-where {{ color: {SKY}; font-size: .95rem; }}

/* Landing hero: the headline beside a live demo blink. */
.sc-hero h1 {{ font-size: clamp(1.8rem, 3vw, 2.8rem); line-height: 1.05; font-weight: 700;
  letter-spacing: -.025em; margin: .4rem 0 1rem 0; color: {TEXT}; max-width: 12ch; }}
.sc-hero p {{ font-size: 1.12rem; line-height: 1.6; color: {SKY}; max-width: 34rem; }}
.sc-steps {{ list-style: none; counter-reset: step; padding: 0; margin: 1.4rem 0 0 0;
  max-width: 34rem; }}
.sc-steps li {{ counter-increment: step; position: relative; padding: .55rem 0 .55rem 2.6rem;
  border-top: 1px solid {STEEL}66; line-height: 1.5; }}
.sc-steps li::before {{ content: counter(step); position: absolute; left: 0; top: .45rem;
  width: 1.7rem; height: 1.7rem; border-radius: 50%; border: 1px solid {YELLOW};
  color: {YELLOW}; display: grid; place-items: center; font-size: .85rem;
  font-family: 'Bricolage Grotesque', sans-serif; }}
.sc-mobile-hint {{ display: none; }}
.sc-howto {{ margin-top: .2rem; max-width: 34rem; }}
.sc-howto h2 {{ font-size: clamp(1.5rem, 2.2vw, 2.1rem); line-height: 1.1; margin: .6rem 0 .6rem 0;
  color: {TEXT}; }}
.sc-howto summary {{ cursor: pointer; color: {YELLOW}; font-weight: 700; width: max-content;
  padding: .3rem 0; list-style: none; }}
.sc-howto summary::-webkit-details-marker {{ display: none; }}
.sc-howto summary::before {{ content: "+"; display: inline-block; width: 1.1rem;
  font-weight: 700; }}
.sc-howto[open] summary::before {{ content: "−"; }}

/* The stage: pictures sit on a darkroom panel and keep their pixels crisp. */
[data-testid="stMain"] [data-testid="stImage"] img {{ background: {DARKROOM};
  border: 1px solid {STEEL}; border-radius: 14px; padding: 6px;
  image-rendering: pixelated; }}
.sc-demo-note {{ color: {SKY}; font-size: .9rem; margin-top: .5rem; }}

/* One readout strip instead of a row of cards. */
.sc-readout {{ display: flex; flex-wrap: wrap; margin: .2rem 0 1rem 0;
  border: 1px solid {STEEL}; border-radius: 14px; background: {INDIGO}40; }}
.sc-readout div {{ flex: 1 1 10rem; padding: .7rem 1.1rem; border-left: 1px solid {STEEL}66; }}
.sc-readout div:first-child {{ border-left: none; }}
.sc-readout b {{ display: block; font-family: 'Bricolage Grotesque', sans-serif;
  font-size: 1.45rem; font-weight: 700; color: {TEXT}; line-height: 1.2; }}
.sc-readout span {{ color: {SKY}; font-size: .85rem; }}

/* Sidebar headings: sentence case, quiet. */
.sc-side-h {{ font-family: 'Bricolage Grotesque', sans-serif; font-weight: 700;
  font-size: 1.05rem; color: {SKY}; margin: 1.1rem 0 .2rem 0; }}
[data-testid="stSidebar"] [data-testid="stForm"] {{ border: none; padding: 0; }}

/* Colour-tab legend swatches. */
.sc-band {{ display: flex; align-items: center; gap: .55rem; margin: .25rem 0;
  color: {SKY}; font-size: .92rem; }}
.sc-band span {{ width: .9rem; height: .9rem; border-radius: 3px; display: inline-block; }}

/* Yellow buttons need dark text; keyboard focus is always visible. */
.stButton button[kind="primary"], .stFormSubmitButton button,
.stDownloadButton button {{ color: {NAVY} !important; background: {YELLOW} !important;
  border: none !important; font-weight: 700; }}
:focus-visible {{ outline: 2px solid {YELLOW} !important; outline-offset: 2px; }}
.stTabs [data-baseweb="tab"] {{ font-size: 1rem; }}
.stTabs [aria-selected="true"] {{ color: {YELLOW} !important; }}

.sc-footer {{ color: {SKY}; opacity: .8; font-size: .82rem; max-width: 75ch;
  border-top: 1px solid {STEEL}66; padding-top: .9rem; margin-top: 2.5rem; }}
.sc-footer p {{ margin: 0 0 .4rem 0; }}

/* Guidebook: a fixed button in the bottom-right corner that opens a glossary. */
.sc-guide {{ position: fixed; right: 1.4rem; bottom: 1.4rem; z-index: 999990;
  max-width: calc(100vw - 2rem); }}
.sc-guide summary {{ list-style: none; cursor: pointer; margin-left: auto; width: max-content;
  background: {YELLOW}; color: {NAVY}; font-weight: 700; padding: .6rem 1.1rem;
  border-radius: 999px; box-shadow: 0 6px 20px {DARKROOM}; font-family:
  'Bricolage Grotesque', sans-serif; }}
.sc-guide summary::-webkit-details-marker {{ display: none; }}
.sc-guide summary::before {{ content: "?"; display: inline-grid; place-items: center;
  width: 1.3rem; height: 1.3rem; margin-right: .45rem; border-radius: 50%;
  background: {NAVY}; color: {YELLOW}; font-size: .85rem; }}
.sc-guide[open] summary::before {{ content: "×"; }}
.sc-guide-body {{ position: absolute; right: 0; bottom: calc(100% + .7rem);
  width: min(26rem, calc(100vw - 1.6rem)); max-height: min(72vh, 40rem); overflow-y: auto;
  padding: 1rem 1.2rem; border-radius: 14px; background: {INDIGO};
  border: 1px solid {STEEL}; box-shadow: 0 12px 40px {DARKROOM}; }}
.sc-guide-body h3 {{ font-size: 1.2rem; margin: 0 0 .4rem 0; color: {TEXT}; }}
.sc-guide-body h4 {{ font-family: 'Bricolage Grotesque', sans-serif; font-size: 1rem;
  color: {YELLOW}; margin: 1rem 0 .3rem 0; }}
.sc-guide-body dl {{ margin: 0; }}
.sc-guide-body dt {{ font-weight: 700; color: {TEXT}; margin-top: .55rem; }}
.sc-guide-body dd {{ margin: .1rem 0 0 0; color: {SKY}; font-size: .9rem; line-height: 1.45; }}

/* Slim notices: a coloured edge on a faint tint, instead of heavy alert boxes. */
.sc-notice {{ border-left: 3px solid {STEEL}; background: {INDIGO}33; color: {TEXT};
  padding: .6rem .9rem; border-radius: 0 10px 10px 0; margin: 0 0 .9rem 0;
  font-size: .95rem; line-height: 1.5; max-width: 75ch; }}
.sc-notice b {{ color: {TEXT}; }}
.sc-demo {{ border-left-color: {YELLOW}; background: {YELLOW}14; }}
.sc-demo b {{ color: {YELLOW}; }}
.sc-warn {{ border-left-color: #F0B37E; background: #F0B37E14; }}

/* Sidebar: captions and help text in pale sky (grey captions measured 4.4:1 there). */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: {SKY} !important;
  opacity: 1 !important; }}
[data-testid="stMain"] [data-testid="stCaptionContainer"],
[data-testid="stMain"] [data-testid="stCaptionContainer"] p {{ color: {SKY} !important;
  opacity: 1 !important; font-size: .9rem; line-height: 1.55; }}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{ font-size: .92rem; }}

/* Phones: tighter edges, stacked readout, and a pointer to the hidden controls. */
@media (max-width: 640px) {{
  .block-container {{ padding: 3.6rem .8rem 2rem .8rem; }}
  .sc-word {{ font-size: 1.6rem; }}
  .sc-logo {{ width: 2rem; height: 2rem; }}
  .sc-hero h1 {{ max-width: none; }}
  .sc-readout div {{ flex: 1 1 0; padding: .5rem .55rem; }}
  .sc-readout b {{ font-size: 1.1rem; }}
  .sc-readout span {{ font-size: .72rem; line-height: 1.25; display: block; }}
  .sc-mobile-hint {{ display: block; margin: 0 0 .8rem 0; padding: .7rem .9rem;
    border: 1px solid {YELLOW}88; border-radius: 10px; color: {TEXT}; }}
  .stTabs [data-baseweb="tab"] {{ font-size: .9rem; }}
  .sc-guide {{ right: .8rem; bottom: .8rem; }}
  .sc-guide summary {{ padding: .45rem .85rem; font-size: .9rem; min-height: 44px;
    display: flex; align-items: center; }}
  .block-container {{ padding-bottom: 5.5rem; }}
}}
</style>""", unsafe_allow_html=True)


def side_heading(text):
    """A quiet sentence-case heading in the sidebar."""
    st.markdown(f'<div class="sc-side-h">{text}</div>', unsafe_allow_html=True)


# ---- Guidebook (fixed button, bottom right) ---------------------------------------
GLOSSARY = [
    # Wording follows ASD-STE100 (Simplified Technical English) at about half strength:
    # short sentences, one idea each, active voice, no idioms.
    ("Position on the sky", [
        ("RA and Dec", "Right ascension (RA) and declination (Dec) give a position on the "
         "sky. They work like longitude and latitude. RA goes from 0° to 360°. Dec goes "
         "from -90° to +90°."),
        ("Arcsecond (\")", "An arcsecond is a very small angle. One degree has 3600 "
         "arcseconds. One SPHEREx pixel covers about 6.2\"."),
        ("Field of view", "The field of view is the width of the sky area in the picture. "
         "It is in degrees. 0.1° is 6 arcminutes."),
        ("Proper motion", "Proper motion is the movement of a star across the sky. The unit "
         "is milliarcseconds per year (mas/yr). Near stars and brown dwarfs move the most."),
        ("Parallax", "Parallax is a small shift in the position of a star during the year. "
         "Earth's orbit causes it. A large parallax shows a near star."),
    ]),
    ("SPHEREx data", [
        ("SPHEREx", "SPHEREx is a NASA space telescope. It records the full sky in 102 "
         "infrared colours, from 0.75 µm to 5 µm."),
        ("µm (micrometre)", "The micrometre is the unit of wavelength in this tool. Our eyes "
         "see 0.4 µm to 0.7 µm. SPHEREx sees longer, infrared wavelengths."),
        ("Detector (D1 to D6)", "SPHEREx has six detectors. Each detector records a "
         "different range of wavelengths. D1 is the shortest (0.75 µm). D6 is the longest "
         "(5 µm)."),
        ("Frame", "A frame is one SPHEREx image of the sky area. It has one time and one "
         "wavelength."),
        ("Epoch", "An epoch is one observation period. SPHEREx observes each sky area again "
         "after about six months."),
        ("Data release (DR1, QR3)", "A data release is a published set of data. The search "
         "includes new releases automatically."),
        ("MJD", "MJD (Modified Julian Date) is a day count. Astronomers use it to calculate "
         "time between dates."),
    ]),
    ("Pictures", [
        ("Blink", "Blink shows images of the same sky area, one after the other. Stars stay "
         "in position. An object that moves or changes is easy to see."),
        ("Epoch stack", "An epoch stack adds all frames of one epoch together. The result is "
         "a clearer picture with less noise."),
        ("Difference image", "A difference image subtracts an earlier image from a later "
         "image. Red shows an increase in brightness. Blue shows a decrease. A red spot "
         "next to a blue spot shows an object that moved."),
        ("False colour", "Our eyes cannot see infrared light. This tool shows short, middle "
         "and long wavelengths as blue, green and red. Usual stars look white. An unusual "
         "object has a colour."),
        ("Spectrum", "A spectrum shows brightness at each wavelength. Its shape gives "
         "information about temperature. It can also show molecules, for example water."),
        ("Candidate", "A candidate is a possible finding. Experts must examine it before it "
         "is a confirmed result."),
    ]),
    ("Other catalogues", [
        ("Gaia DR3", "Gaia DR3 is a star catalogue from the ESA Gaia mission. It uses visible "
         "light. It gives position, distance and motion for almost two billion stars."),
        ("G magnitude", "G magnitude is the brightness in Gaia. A smaller number is a "
         "brighter star. A difference of 5 magnitudes is a factor of 100."),
        ("BP-RP", "BP-RP is a Gaia colour. A larger value shows a redder, cooler star."),
        ("RUWE", "RUWE shows how well Gaia data fits one single star. A value above 1.4 can "
         "show a second star or a bad fit."),
        ("WISE and CatWISE2020", "WISE is a NASA infrared space telescope. CatWISE2020 is a "
         "catalogue of WISE sources. It includes their motion."),
        ("W1 and W2", "W1 and W2 are WISE brightness values. W1 is at 3.4 µm. W2 is at "
         "4.6 µm."),
        ("W1 - W2", "W1 - W2 is a WISE colour. A value above 1 can show a cold brown dwarf. "
         "WISE 0855 has a value near 4."),
        ("Contamination flags", "Contamination flags show possible false sources in WISE. "
         "D: diffraction spike. P: persistence. H: halo of a bright star. O: optical ghost. "
         "0: clean."),
    ]),
]


def guidebook():
    """A Guide button fixed to the bottom-right corner; it opens a glossary panel.
    Plain HTML <details>, so it works without JavaScript and with the keyboard."""
    sections = "".join(
        f"<h4>{title}</h4><dl>"
        + "".join(f"<dt>{term}</dt><dd>{text}</dd>" for term, text in terms)
        + "</dl>" for title, terms in GLOSSARY)
    st.markdown(f'<details class="sc-guide"><summary>Guide</summary>'
                f'<div class="sc-guide-body"><h3>Guide to the terms</h3>{sections}</div>'
                f"</details>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def landing_blink():
    """A small blink made from the demo generator, so the first thing a visitor sees is
    the tool doing its job. Built once, then remembered."""
    coord = SkyCoord(ra=133.760098 * u.deg, dec=-7.242266 * u.deg, frame="icrs")
    fov = 0.08
    urls, mjds = demo_data.find_images(coord, round(fov * core.CUTOUT_FACTOR, 4), "D2")
    frames, _ = demo_data.download_full(
        [{"url": x, "mjd": m} for x, m in zip(urls, mjds)], coord)
    target_wcs, shape = core.make_target_wcs(coord, fov)
    stacks = core.stack_epochs(core.align_frames(frames, target_wcs, shape), 45, "mean")
    for s_ in stacks:
        s_["label"] = f"Demo, epoch {s_['epoch']}"
    return core.make_gif_bytes(stacks, 700, width_px=720,
                               decorate=lambda pic, frame: core.add_watermark(pic))


# ---- State and helpers -------------------------------------------------------
def init_state():
    qp = st.query_params
    # Default: WISE 0855 moved forward to DISPLAY_EPOCH (see PRESETS).
    st.session_state.setdefault("ra_text", qp.get("ra", "133.760098"))
    st.session_state.setdefault("dec_text", qp.get("dec", "-7.242266"))
    try:
        fov0 = float(qp.get("fov", FOV_DEFAULT))
    except ValueError:
        fov0 = FOV_DEFAULT
    st.session_state.setdefault("fov", round(min(max(fov0, FOV_MIN), FOV_MAX), 2))
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("notice", None)
    st.session_state.setdefault(  # ?gaia=1 / ?wise=1 in the URL switch overlays on
        "catalogs", [c for c, k in ((CAT_GAIA, "gaia"), (CAT_WISE, "wise"))
                     if qp.get(k) == "1"])
    st.session_state.setdefault(  # ?demo=1 in the URL opens the fake demo data
        "source", SOURCE_DEMO if qp.get("demo") == "1" else SOURCE_REAL)


def apply_preset():
    """Fill the RA/Dec boxes when a quick target is chosen."""
    name = st.session_state.get("preset", "(custom)")
    st.session_state["preset_error"] = None
    if name not in PRESETS:
        return
    try:
        ra, dec, note = preset_position(PRESETS[name])
        st.session_state["ra_text"] = f"{ra:.6f}"
        st.session_state["dec_text"] = f"{dec:.6f}"
        st.session_state["preset_error"] = note
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


@st.cache_data(ttl=3600, show_spinner=False)
def cached_gaia(ra_deg, dec_deg, fov_deg):
    """Gaia DR3 stars in the view (remembered for an hour)."""
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    return core.find_gaia(coord, fov_deg)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_wise(ra_deg, dec_deg, fov_deg):
    """CatWISE2020 sources in the view (remembered for an hour)."""
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    return core.find_wise(coord, fov_deg)


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
        candidates, n_probe_failed = engine(p).probe_frames(urls, mjds, coord,
                                                            progress=on_progress)
        bar.empty()
        if not candidates:
            fail(status, "No frames arrived in time", "error",
                 f"None of the wavelength checks finished within {core.PROBE_DEADLINE_S} "
                 "seconds. Please wait a few minutes and press Fetch again (anything already "
                 "downloaded is kept), or try the demo data source meanwhile.")
            return None

    chosen, wl_used = core.choose_frames(candidates, None, tol, p["how_many"], p["gap"])
    result = {"mode": MATCH_MODE, "candidates": candidates, "frames": [], "aligned": [],
              "chosen_urls": {f["url"] for f in chosen}, "wl_used": wl_used,
              "n_found": n_found, "n_failed": n_probe_failed, "p": p}

    if len(chosen) < 2:
        fail(status, "Too few frames match", "warning",
             f"Only {len(chosen)} frame(s) satisfy the settings. Lower the minimum days apart, "
             "widen the wavelength tolerance, turn wavelength matching off, or try 'Epoch "
             "stacks' mode. (The Frames tab shows what was found.)", result)
        return None

    # Step 2: download full-size cutouts for the chosen frames only.
    bar, on_progress = make_progress("Downloading frames")
    frames, n_failed = engine(p).download_full(chosen, coord, progress=on_progress)
    bar.empty()
    result["frames"], result["n_failed"] = frames, n_probe_failed + n_failed

    if len(frames) < 2:
        fail(status, "Too few frames arrived", "error",
             "Too few frames arrived in time. Please wait a minute and press Fetch again; "
             "frames already downloaded are kept.", result)
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
    frames, n_failed = engine(p).download_full(candidates, coord, progress=on_progress)
    bar.empty()

    if len(frames) < 2:
        fail(status, "Too few frames arrived", "error",
             "Too few frames arrived in time. Please wait a minute and press Fetch again; "
             "frames already downloaded are kept, so each try gets further.")
        return None

    target_wcs, shape = core.make_target_wcs(coord, p["fov"])
    aligned_frames = core.align_frames(frames, target_wcs, shape)
    stacks = core.stack_epochs(aligned_frames, p["epoch_gap"], p["combine"])

    epoch_of = {x: s["epoch"] for s in stacks for x in s["urls"]}
    for f in frames:
        f["epoch"] = epoch_of.get(f["url"])
    result = {"mode": EPOCH_MODE, "candidates": frames, "frames": frames, "aligned": stacks,
              "aligned_frames": aligned_frames,   # single frames, for the Colour tab
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
    """Search, download cutouts, build the frames. Stores the result for display."""
    st.session_state["result"] = None
    st.session_state["notice"] = None

    try:
        coord = core.parse_coord(p["ra_text"], p["dec_text"])
    except ValueError as err:
        st.session_state["notice"] = ("error", str(err))
        return

    cutout = round(p["fov"] * core.CUTOUT_FACTOR, 4)

    status_slot = st.empty()   # cleared once the frames are ready
    with status_slot.status("Fetching SPHEREx frames...", expanded=True) as status:
        try:
            if p.get("source") == SOURCE_DEMO:
                st.write("Making demo frames...")
                urls, mjds = demo_data.find_images(coord, cutout, p["detector"])
            else:
                st.write("Searching SPHEREx images (about 20 s)...")
                urls, mjds = cached_find(round(coord.ra.deg, 6), round(coord.dec.deg, 6),
                                         cutout, p["detector"])
        except TimeoutError:
            fail(status, "Search timed out", "error",
                 f"The image search did not finish within {core.TAP_TIMEOUT_S} seconds. "
                 "Please wait a few minutes and press Fetch again, or try the demo data "
                 "source meanwhile.")
            return
        except Exception as err:
            if p.get("source") == SOURCE_DEMO:  # demo mode never talks to IRSA
                fail(status, "Demo data could not be generated", "error",
                     f"The demo data could not be generated ({type(err).__name__}: {err}). "
                     "If the code was just updated, stop the app (Ctrl+C in the terminal) "
                     "and start it again.")
            elif core.is_busy_error(err):
                fail(status, "Search unavailable right now", "error",
                     f"The image search is unavailable at the moment ({err}); the app tried "
                     "3 times. Please wait a few minutes and press Fetch again, or try the "
                     "demo data source meanwhile.")
            else:
                fail(status, "Search did not complete", "error",
                     f"The image search did not complete ({err}). Please wait a moment and "
                     "press Fetch again.")
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
    status_slot.empty()
    # Put the position in the URL so the view can be shared or embedded.
    st.query_params["ra"] = f"{coord.ra.deg:.6f}"
    st.query_params["dec"] = f"{coord.dec.deg:.6f}"
    st.query_params["fov"] = f"{p['fov']:.2f}"
    for key, on in (("gaia", CAT_GAIA in p.get("catalogs", [])),
                    ("wise", CAT_WISE in p.get("catalogs", [])),
                    ("demo", p.get("source") == SOURCE_DEMO)):
        if on:
            st.query_params[key] = "1"
        elif key in st.query_params:
            del st.query_params[key]


def describe(f):
    """One-line description of a displayed frame (single frame or epoch stack)."""
    if f.get("label"):
        return f["label"]
    return f"{Time(f['mjd'], format='mjd').iso[:10]} | {f['wavelength']:.2f} um"


# ---- Page --------------------------------------------------------------------
init_state()

inject_css()
guidebook()
mast_slot = st.empty()   # filled with the header once there is a result

with st.sidebar:
    side_heading("Target")
    st.selectbox("Quick targets", ["(custom)", *PRESETS], key="preset", on_change=apply_preset)
    if st.session_state.get("preset_error"):
        st.warning(st.session_state["preset_error"])

    with st.form("controls"):
        st.text_input("Right ascension (RA)", key="ra_text",
                      help="Degrees (133.795) or hours (08h55m10.83s)")
        st.text_input("Declination (Dec)", key="dec_text",
                      help="Degrees (-7.245) or degrees-minutes-seconds (-07d14m42.5s)")
        st.slider("Field of view (degrees)", FOV_MIN, FOV_MAX, step=0.01, key="fov",
                  help="Wider views download more data. 0.1° = 6 arcmin.")

        side_heading("Data")
        detector = st.selectbox("Detector", core.DETECTORS, index=1,
                                help="D1 is the bluest (0.75 µm), D6 the reddest (5 µm). "
                                     "Cold brown dwarfs are faint in D1-D4.")
        source = st.radio("Data source", [SOURCE_REAL, SOURCE_DEMO], key="source",
                          help="Demo invents SPHEREx-like frames to try every feature "
                               "offline. It is not real data.")
        release = st.selectbox("Data release", ["All releases", "dr1", "qr3"])
        catalogs = st.multiselect(
            "Star catalogues to overlay", [CAT_GAIA, CAT_WISE], key="catalogs",
            placeholder="None (SPHEREx pictures only)",
            help="Gaia suits bright stars. WISE also sees cold brown dwarfs that Gaia "
                 "misses.")

        side_heading("Animation")
        mode = st.radio("Mode", [MATCH_MODE, EPOCH_MODE], key="mode",
                        help="Matched: a few frames at one wavelength. Epoch stacks: each "
                             "observing period added together (needed for the Colour tab).")

        with st.expander("Matched-wavelength settings"):
            gap = st.slider("Minimum days between frames", 0, 60, 10)
            wl_mode = st.selectbox("Wavelength matching", ["Auto (most frames)", "Off"],
                                   help="Off mixes wavelengths, so stars can flicker.")
            tol = st.slider("Wavelength tolerance (µm)", 0.02, 0.50, 0.10, step=0.01)
            how_many = st.slider("Maximum frames", 2, 16, 8)

        with st.expander("Epoch-stack settings"):
            epoch_gap = st.slider("Epoch gap (days)", 5, 120, 45,
                                  help="Frames closer than this form one epoch.")
            combine = st.radio("Combine frames by", ["Mean", "Median"], horizontal=True,
                               help="Mean keeps fast movers; median removes them.")

        submitted = st.form_submit_button("Fetch frames", type="primary", width="stretch")

# Open straight to a result when the page is loaded from a link with ra/dec in it.
autoload = False
if "autoloaded" not in st.session_state:
    st.session_state["autoloaded"] = True
    autoload = "ra" in st.query_params and "dec" in st.query_params

if submitted or autoload:
    run_fetch({"ra_text": st.session_state["ra_text"], "dec_text": st.session_state["dec_text"],
               "fov": round(float(st.session_state["fov"]), 3), "detector": detector,
               "release": release, "catalogs": catalogs, "source": source, "mode": mode,
               "gap": gap, "wl_mode": wl_mode, "tol": tol,
               "how_many": how_many, "epoch_gap": epoch_gap, "combine": combine.lower()})

notice = st.session_state.get("notice")
if notice:
    (st.error if notice[0] == "error" else st.warning)(notice[1])

result = st.session_state.get("result")
if result is None:
    mast_slot.markdown(wordmark(), unsafe_allow_html=True)
    if not notice:
        # Phones only: point to the hidden controls before anything else.
        st.markdown('<div class="sc-mobile-hint">Tap the arrow at the top left to open the '
                    "controls.</div>", unsafe_allow_html=True)
        # Picture first (next to the sidebar); the intro folds away beside it.
        pic_col, info_col = st.columns([5, 2], gap="large")
        pic_col.image(landing_blink(), width="stretch")
        info_col.markdown("""
<div class="sc-hero">
  <details class="sc-howto"><summary>About this tool</summary>
  <h2>See what moves in the infrared sky.</h2>
  <p>Blink NASA SPHEREx images of any patch of sky. Asteroids, comets and nearby brown
  dwarfs jump out against the fixed stars.</p>
  <ol class="sc-steps">
    <li>Pick a quick target, or type any RA and Dec.</li>
    <li>Choose a detector and, if you want, star catalogues.</li>
    <li>Press <b>Fetch frames</b>, then blink, compare and explore.</li>
  </ol>
  </details>
  <div class="sc-demo-note">Demo data: the moving object and the asteroid are invented.</div>
</div>""", unsafe_allow_html=True)
    st.stop()

aligned = result["aligned"]
p = result["p"]
is_epoch = result["mode"] == EPOCH_MODE

mast_slot.markdown(wordmark(f'<span class="sc-where">RA {p["ra_text"]}, Dec {p["dec_text"]}, '
                            f'{p["fov"]:.2f}° wide</span>'), unsafe_allow_html=True)

def show_notice(html, tone="note"):
    """A slim notice with a coloured edge: quieter than Streamlit's alert boxes."""
    st.markdown(f'<div class="sc-notice sc-{tone}" role="note">{html}</div>',
                unsafe_allow_html=True)


if p.get("source") == SOURCE_DEMO:
    show_notice("<b>DEMO MODE:</b> invented data, not SPHEREx observations."
                + (" Catalogue markers show real stars." if p.get("catalogs") else ""),
                "demo")

# ---- Summary line and warnings -------------------------------------------------
def readout(items):
    """One strip of (value, label) pairs, instead of a row of identical cards."""
    st.markdown('<div class="sc-readout">' + "".join(
        f"<div><b>{v}</b><span>{label}</span></div>" for v, label in items) + "</div>",
        unsafe_allow_html=True)


lo_um, hi_um = core.DETECTOR_BANDS_UM[p["detector"]]
if is_epoch:
    readout([(p["detector"], f"{lo_um}-{hi_um} µm"), (len(aligned), "epochs"),
             (len(result["frames"]), "frames used"), (result["n_found"], "images found")])
    blink_note = f"{p['combine'].capitalize()} stack of each observing period."
else:
    readout([(p["detector"], f"{lo_um}-{hi_um} µm"), (len(aligned), "frames in blink"),
             (len(result["candidates"]), "frames checked"),
             (result["n_found"], "images found")])
    blink_note = ((f"Frames at ≈ {result['wl_used']:.2f} µm" if result["wl_used"]
                   is not None else "Frames at any wavelength")
                  + f", ≥ {p['gap']} days apart.")
if result["n_failed"]:
    st.caption(f"{result['n_failed']} image(s) did not arrive. Press Fetch again to retry.")

if not is_epoch and p["wl_mode"] == "Off":
    show_notice("<b>Wavelength matching is off.</b> Flicker can come from colour, not "
                "motion.", "warn")

if is_epoch and aligned:
    counts = [s["n"] for s in aligned]
    thin = [s["epoch"] for s in aligned if s["n"] < 0.5 * max(counts)]
    if thin:
        show_notice(f"Epoch(s) {', '.join(map(str, thin))} have far fewer frames. "
                    "Differences there may not be real.", "warn")


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


def show_spectrum(pts, detector):
    """Brightness of the centre target against wavelength, coloured by epoch."""
    import altair as alt  # installed with Streamlit

    unit = pts[0]["unit"]
    df = pd.DataFrame(pts)
    df["Epoch"] = df["epoch"].map(lambda k: f"Epoch {k}")
    df["low"], df["high"] = df["flux"] - df["flux_err"], df["flux"] + df["flux_err"]
    binned = pd.DataFrame(core.bin_spectrum(pts, detector))

    x = alt.X("wavelength:Q", title="Wavelength (µm)", scale=alt.Scale(zero=False))
    y_title = f"Brightness (3×3 px sum, {unit})"
    bars = alt.Chart(df).mark_rule(opacity=0.5).encode(
        x=x, y=alt.Y("low:Q", title=y_title), y2="high:Q", color="Epoch:N")
    dots = alt.Chart(df).mark_circle(size=90, opacity=1).encode(
        x=x, y="flux:Q", color=alt.Color("Epoch:N", title="Observed"),
        tooltip=["date", alt.Tooltip("wavelength:Q", format=".3f"),
                 alt.Tooltip("flux:Q", format=".2f"), alt.Tooltip("flux_err:Q", format=".2f")])
    layers = [bars, dots]
    if len(binned) > 1:
        layers.append(alt.Chart(binned).mark_line(color=LIGHT_BLUE, strokeDash=[4, 3]).encode(
            x="wavelength:Q", y="flux:Q"))
    st.altair_chart(alt.layer(*layers).interactive(), width="stretch")

    st.caption(f"{len(pts)} measurements, {df['epoch'].nunique()} epoch(s). Dot = one "
               "frame, colour = epoch. Dashed line = median in 17 bins. Bars = ±1σ.")
    st.caption("**Quick-look only:** a 3×3-pixel box with the background removed. Nearby "
               "stars and motion faster than ~10\"/yr affect it. Any feature is a "
               "**candidate**.")
    csv = df[["date", "mjd", "epoch", "wavelength", "bandwidth", "flux", "flux_err",
              "unit"]].to_csv(index=False).encode()
    st.download_button("Download spectrum (CSV)", csv, file_name="scintillation_spectrum.csv",
                       mime="text/csv")


def show_colour(result, p, decorate):
    """False-colour picture per epoch, from 3 wavelength groups of one detector."""
    if result["mode"] != EPOCH_MODE or not result.get("aligned_frames"):
        st.info("The colour view needs **Epoch stacks** mode. Change the mode and press "
                "Fetch again.")
        return
    lo, hi = core.DETECTOR_BANDS_UM[p["detector"]]
    by_url = {f["url"]: f for f in result["aligned_frames"]}
    stacks = result["aligned"]
    pic_col, side = st.columns([5, 2], gap="large")
    with side:
        k = st.radio("Epoch", range(len(stacks)), horizontal=True,
                     format_func=lambda i: f"Epoch {stacks[i]['epoch']}")
        stack = stacks[k]
        frames = [by_url[x] for x in stack["urls"] if x in by_url]
        img, summary = core.colour_image(frames, p["detector"],
                                         label=f"Epoch {stack['epoch']}")
        st.markdown(f"{stack['dates']}, {p['detector']} ({lo}-{hi} µm) split in three:")
        swatch = {"blue": "#6FB3D9", "green": "#8FD694", "red": "#F08A8A"}
        st.markdown("".join(
            f'<div class="sc-band"><span style="background:{swatch[s["colour"]]}"></span>'
            f'{s["colour"].capitalize()}: {s["from_um"]:.2f}-{s["to_um"]:.2f} µm, '
            f'{s["frames"]} frame{"s" if s["frames"] != 1 else ""}</div>'
            for s in summary), unsafe_allow_html=True)
    if img is None:
        pic_col.warning(f"Epoch {stack['epoch']}: too few frames for a colour picture.")
    else:
        # Gaia markers use the epoch's mean date.
        pic_col.image(decorate(img, stack) if decorate else img, width="stretch")
    side.caption("**False colour.** Ordinary stars look white; an unusual spectrum gives "
                 "a tint. A fast mover shows as separate red, green and blue dots. A tinted "
                 "object is a **candidate**: check its spectrum.")


def epoch_table():
    return pd.DataFrame([{"Epoch": s["epoch"], "Dates": s["dates"], "Frames": s["n"],
                          "Wavelength range (um)": f"{s['wl_min']:.2f} - {s['wl_max']:.2f}"}
                         for s in aligned])


# ---- Catalogue overlays (Gaia DR3, CatWISE2020) -------------------------------
# Tabs are created here, before the overlay code, so the catalogue panels can live
# in their own tab while their markers are ready for every picture tab.
if aligned:
    (tab_auto, tab_manual, tab_compare, tab_spec, tab_colour, tab_cat,
     tab_table) = st.tabs(["Blink", "Step through", "Compare", "Spectrum",
                          "Colour", "Catalogues", "Frames"])
cat_ctx = tab_cat if aligned else st.container()
with cat_ctx:
    if aligned and not p.get("catalogs"):
        st.info("Choose star catalogues in the sidebar, then press Fetch frames.")
    decorators = []   # functions that draw catalogue markers on a picture
    gaia_rows = wise_rows = None
    gaia_target = wise_target = None
    catalogs = p.get("catalogs", [])
    if aligned and catalogs:
        view = core.parse_coord(p["ra_text"], p["dec_text"])
        target_wcs, (n_pix, _) = core.make_target_wcs(view, p["fov"])
        mid_mjd = float(np.mean([f["mjd"] for f in aligned]))

    if aligned and CAT_GAIA in catalogs:
        try:
            with st.spinner("Asking the Gaia archive for stars in this view..."):
                gaia = cached_gaia(round(view.ra.deg, 6), round(view.dec.deg, 6), p["fov"])
        except Exception as err:
            gaia = None
            st.warning("Gaia stars could not be loaded. Press Fetch again later.")

        if gaia is not None and len(gaia["ra"]) == 0:
            st.info("Gaia DR3 has no stars in this view.")
        elif gaia is not None:
            st.subheader("Gaia DR3 stars")
            target, target_sep = core.nearest_gaia(gaia, view, mid_mjd)
            gaia_target = target

            # Star picker: the target first, then the brightest stars.
            order = [int(k) for k in np.argsort(np.nan_to_num(gaia["gmag"], nan=99.0))[:300]]
            if target is not None:
                order = [target] + [k for k in order if k != target]

            def star_name(k):
                g = gaia["gmag"][k]
                tag = "TARGET (centre of view)  |  " if k == target else ""
                return f"{tag}G {g:.1f}  |  Gaia DR3 {gaia['source_id'][k]}"

            col_g1, col_g2 = st.columns([1, 1])
            mag_limit = col_g1.slider("Faintest Gaia G magnitude to circle", 8.0, 21.0, 16.0,
                                      step=0.5, help="Higher = fainter stars too.")
            picked = col_g2.selectbox("Star to describe (magenta box)", order,
                                      format_func=star_name,
                                      help="The target is the Gaia star at the centre.")

            decorators.append(lambda pic, frame: core.add_gaia_markers(
                pic, gaia, frame["mjd"], target_wcs, n_pix, mag_limit, highlight=picked))

            if target is None:
                st.info(f"**No Gaia star at the centre** (nearest {target_sep:.0f}\"). Gaia "
                        "misses very cold objects and solar-system bodies, so a SPHEREx source "
                        "here is a **candidate**.")

            with st.expander(f"Details: {star_name(picked)}", expanded=True):
                col_a, col_b = st.columns(2)
                facts = core.gaia_details(gaia, picked)
                half = (len(facts) + 1) // 2
                for col, part in ((col_a, facts[:half]), (col_b, facts[half:])):
                    col.dataframe(pd.DataFrame(part, columns=["Property", "Value"]),
                                  hide_index=True)
                # Unverified: on 15 Nov 2026 every CDS web page (even the SIMBAD home page)
                # returned 404, so this link format could not be tested.
                simbad = ("https://simbad.cds.unistra.fr/simbad/sim-coo?Coord="
                          + quote(f"{gaia['ra'][picked]:.6f} {gaia['dec'][picked]:+.6f}")
                          + "&Radius=5&Radius.unit=arcsec")
                st.markdown(f"More about this star: [SIMBAD (names, papers)]({simbad})")

            pm = np.hypot(gaia["pmra"], gaia["pmdec"])
            gaia_rows = pd.DataFrame({
                "Gaia DR3 source": gaia["source_id"],
                "G (mag)": np.round(gaia["gmag"], 2),
                "BP-RP": np.round(gaia["bp_rp"], 2),
                "Proper motion (mas/yr)": np.where(gaia["has_pm"], np.round(pm, 1), np.nan),
                "Parallax (mas)": np.round(gaia["parallax"], 2),
                "RA (2016.0)": np.round(gaia["ra"], 6), "Dec (2016.0)": np.round(gaia["dec"], 6),
            }).sort_values("G (mag)")
            n_marked = int(np.sum(gaia["gmag"] <= mag_limit))
            st.caption(f"{n_marked} stars circled. Cyan = star, orange = fast mover, "
                       f"magenta = selected. Source: {gaia['server']}."
                       + (f" Brightest {core.GAIA_MAX_ROWS} only." if gaia["truncated"]
                          else ""))


    if aligned and CAT_WISE in catalogs:
        try:
            with st.spinner("Asking for WISE infrared sources in this view..."):
                wise = cached_wise(round(view.ra.deg, 6), round(view.dec.deg, 6), p["fov"])
        except Exception as err:
            wise = None
            st.warning("WISE sources could not be loaded. Press Fetch again later.")
        if wise is not None and len(wise["ra"]) == 0:
            st.info("CatWISE2020 has no sources in this view.")
        elif wise is not None:
            st.subheader("WISE infrared sources (CatWISE2020)")
            wise_target, wsep = core.nearest_gaia(wise, view, mid_mjd)
            w_limit = st.slider("Faintest W2 magnitude to mark", 8.0, 17.5, 15.0, step=0.5,
                                help="W2 is the 4.6 µm band. Higher = fainter sources too.")
            decorators.append(lambda pic, frame: core.add_wise_markers(
                pic, wise, frame["mjd"], target_wcs, n_pix, w_limit, highlight=wise_target))
            if wise_target is None:
                st.info(f"No WISE source at the centre (nearest {wsep:.0f}\"). Very bright "
                        "stars saturate WISE; use Gaia for them.")
            else:
                with st.expander(f"WISE source at the centre: {wise['name'][wise_target]}",
                                 expanded=True):
                    facts = core.wise_details(wise, wise_target)
                    st.dataframe(pd.DataFrame(facts, columns=["Property", "Value"]),
                                 hide_index=True)
                    if CAT_GAIA in catalogs and gaia_target is None:
                        st.success("**Seen by WISE but not by Gaia:** bright in infrared, "
                                   "faint in visible light, like a cold brown dwarf. A "
                                   "**candidate** to check.")
            wpm = np.hypot(wise["pmra"], wise["pmdec"])
            wise_rows = pd.DataFrame({
                "CatWISE2020 source": wise["name"],
                "W1 (mag)": np.round(wise["w1"], 2), "W2 (mag)": np.round(wise["w2"], 2),
                "W1 − W2": np.round(wise["w1_w2"], 2),
                "Proper motion (mas/yr)": np.where(wise["has_pm"], np.round(wpm), np.nan),
                "Contamination flags": wise["cc_flags"],
                "RA (2015.4)": np.round(wise["ra"], 6), "Dec (2015.4)": np.round(wise["dec"], 6),
            }).sort_values("W2 (mag)")
            n_w = int(np.sum(wise["w2"] <= w_limit))
            st.caption(f"{n_w} sources marked. Lavender = source, orange = fast mover, "
                       "grey = possible artefact, magenta = centre. Source: CatWISE2020 via "
                       "NOIRLab Astro Data Lab."
                       + (f" Brightest {core.WISE_MAX_ROWS} only." if wise["truncated"]
                          else ""))



def decorate_all(pic, frame):
    for draw in decorators:
        pic = draw(pic, frame)
    return pic


decorate = decorate_all if decorators else None
if p.get("source") == SOURCE_DEMO:
    # Demo pictures come from AI-assisted code, so each one carries a visible
    # watermark (NASA Space Apps SI rule). Real SPHEREx pictures are not marked.
    _markers = decorate
    decorate = lambda pic, frame: core.add_watermark(
        _markers(pic, frame) if _markers else pic)


# ---- Tabs --------------------------------------------------------------------
# Every picture tab uses the same "stage" layout: the picture large on the left
# (about 70% of the page), its controls and notes in a slim column on the right.
# On a phone the two columns stack, picture first.
def stage():
    return st.columns([5, 2], gap="large")


def show_picture(where, pic):
    where.image(pic, width="stretch")


if aligned:
    with tab_auto:
        pic_col, side = stage()
        with side:
            ms = st.slider("Time per frame (ms)", 100, 2000, 500, step=50)
            st.caption(blink_note + " Oldest first. North up, east left.")
        show_picture(pic_col, core.make_gif_bytes(aligned, ms, decorate=decorate))

    with tab_manual:
        pic_col, side = stage()
        with side:
            idx = (st.slider("Frame", 1, len(aligned), 1) if len(aligned) > 1 else 1)
            f = aligned[idx - 1]
            st.caption(f"Frame {idx} of {len(aligned)}: {describe(f)}")
        pic = core.frame_image(f)
        show_picture(pic_col, decorate(pic, f) if decorate else pic)

    with tab_compare:
        if len(aligned) < 2:
            st.info("At least two frames (or epochs) are needed to compare.")
        else:
            pic_col, side = stage()
            names = [describe(f) for f in aligned]
            with side:
                ia = st.selectbox("Image A (earlier)", range(len(aligned)),
                                  format_func=lambda i: names[i], index=0)
                ib = st.selectbox("Image B (later)", range(len(aligned)),
                                  format_func=lambda i: names[i], index=len(aligned) - 1)
                op_label = st.radio("Operation", ["Difference (B − A)", "Sum (A + B)"],
                                    horizontal=True)
                op = "difference" if op_label.startswith("Difference") else "sum"
                sigma = 5.0
                if op == "difference":
                    sigma = st.slider("Stretch (noise levels)", 2.0, 12.0, 5.0, step=0.5,
                                      help="Lower shows fainter changes, with more noise.")
                    st.caption("**Red** = brighter in B, **blue** = fainter in B. A red-blue "
                               "pair is something that moved.")
                else:
                    st.caption("Adding images makes faint sources clearer.")
            if ia == ib:
                pic_col.warning("Pick two different images.")
            else:
                pic = core.combine_image(aligned[ia]["aligned"], aligned[ib]["aligned"],
                                         op, sigma)
                show_picture(pic_col, decorate(pic, aligned[ib]) if decorate else pic)

    with tab_spec:
        # Matched mode: every candidate's tiny probe measured the target, so the
        # spectrum uses all of them, not just the few frames in the blink.
        pts = core.spectrum_points(result["candidates"], p["epoch_gap"])
        if len(pts) < 2:  # matching off: only the downloaded frames were measured
            pts = core.spectrum_points(result["frames"], p["epoch_gap"])
        if len(pts) < 2:
            st.info("Too few measurements for a spectrum. Turn wavelength matching on, or "
                    "use Epoch stacks, then press Fetch again.")
        else:
            show_spectrum(pts, p["detector"])

    with tab_colour:
        show_colour(result, p, decorate)

    with tab_table:
        if is_epoch:
            st.subheader("Epochs")
            st.dataframe(epoch_table(), hide_index=True)
            st.subheader("All downloaded frames")
        st.dataframe(frame_list_table(), hide_index=True)
        if gaia_rows is not None:
            st.subheader(f"Gaia DR3 stars in the view ({len(gaia_rows)})")
            st.dataframe(gaia_rows, hide_index=True)
        if wise_rows is not None:
            st.subheader(f"WISE sources in the view ({len(wise_rows)})")
            st.dataframe(wise_rows, hide_index=True)

elif result["candidates"]:
    # Too few frames matched: still show what was found, so the settings can be tuned.
    st.subheader("Frames found")
    st.dataframe(frame_list_table(), hide_index=True)

st.markdown(
    '<div class="sc-footer"><p><b>Scintillation</b>, NASA Space Apps 2026.</p>'
    "<p>Data: NASA SPHEREx via NASA/IPAC IRSA; ESA Gaia DR3; CatWISE2020 from NASA WISE "
    "and NEOWISE (via NOIRLab Astro Data Lab). Method adapted from the IRSA SPHEREx cutouts "
    "tutorial (BSD-3-Clause). Pictures are for viewing, not measuring. Findings are "
    "<b>candidates</b>, not discoveries.</p>"
    "<p><b>AI use:</b> the code and on-screen text were written with help from Claude "
    "(Anthropic) and reviewed by the team. Demo pictures are synthetic and watermarked. "
    "The logo is the team's own drawing. Full credits and licences: README.md.</p></div>",
    unsafe_allow_html=True)

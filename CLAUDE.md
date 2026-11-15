# CLAUDE.md: Scintillation (NASA Space Apps 2026, "Planet X and SPHEREx")

Context for Claude Code. Last updated 15 Nov 2026, ~21:45 WIB (UTC+7).

**Whose copy this is:** the user on this computer is the **first author** of the project. They wrote the first version of the app and plan the frontend polish. **All the original files live here on this computer**: this folder is the original, and the friend works from copies of it. This file was first written as a handover for the friend's Claude and then repurposed here, so "the second Claude" / "the friend's Claude" below means the friend's, not this one.

## Deadline and priorities
- The hackathon runs 14-15 Nov 2026. **Final submission is due at the end of 15 Nov.** A working, public, honest tool beats extra features.
- Judging criteria: Influence, Creativity, Validity, Relevance, **Presentation** (teams often neglect this one). All external resources, including open-source code, must be listed in the References field.
- Challenge objective: a **public web tool**. The user enters RA/Dec and sees a series of SPHEREx images of that patch, played like a little video, so moving objects are easy to spot.
- Test targets named in the challenge: Barnard's Star, **Epsilon Indi B**, WISE 0855, RW Aurigae, R Draconis.

## How to work with this team
- The builders are **first-time programmers and first-time hackathon participants** (Windows 11, VS Code). Explain step by step and give copy-paste commands (PowerShell on Windows).
- **Python on this machine:** the default `python` is **3.15**, and the libraries do not install on it (PyYAML needs a C++ compiler). Use **3.12** in a venv: `py -3.12 -m venv .venv`, then `.\.venv\Scripts\Activate.ps1`.
- **Say plainly what you tested and what you could not test.** Never present untested code as working.
- Don't invent facts about SPHEREx or IRSA. Check the docs, or label a claim as unverified.
- Results must say **"candidate"**, never "discovery".
- **User-facing wording must be neutral:** never blame a problem on a service (IRSA, ESA, CDS...). Say what happened and what to do ("The image search did not finish within 60 seconds. Please wait a few minutes and press Fetch again, or try the demo data source meanwhile."). The user asked for this on 15 Nov. Internal notes in this file may still describe causes.
- Two people (and two Claudes) share this repo. Keep one copy of the code in GitHub, don't both edit the same file at once, and after every change say which file changed and what.
- **This local folder is not a git clone** (there's no hidden `.git` folder here; GitHub never shows `.git` on its web page anyway). It holds the original files, so treat it as the source of truth unless the user says GitHub is newer. Until it is linked to GitHub, changes made here must be uploaded by hand. Linking it (`git clone`, or `git init` plus a remote) is a team decision, so ask first.

## Run
```
python -m pip install -r requirements.txt
python -m streamlit run app.py
python irsa_health.py          # times each IRSA step: is IRSA slow, or the app?
python -I tests/smoke.py .     # offline test on FAKE demo data, no network (10 checks)
```
(Run these inside the Python 3.12 venv above.) **After any change to `spherex_core.py` or `demo_data.py`, restart Streamlit** (Ctrl+C, then run it again). A running app re-runs `app.py` but can keep the old copy of the other files in memory. On 15 Nov this caused "module 'spherex_core' has no attribute 'DETECTOR_BANDS_UM'" in demo mode.
URL parameters `?ra=&dec=&fov=` open the app already pointed at a position; `&gaia=1` adds the Gaia overlay, `&wise=1` the CatWISE2020 overlay, `&demo=1` opens demo mode.

## Files
| File | Purpose |
|---|---|
| `app.py` | Streamlit page titled **"Scintillation"** (renamed from "SPHEREx Blink" on 15 Nov): RA/Dec input, Data source (real / demo) and **Star catalogues to overlay** (Gaia DR3, CatWISE2020; replaced the Mission radio), FOV slider (0.02-0.20 deg), quick targets, modes, tabs Blink / Step through / Compare / **Spectrum** / **Colour** / **Catalogues** / Frames, plus a **Guide** button bottom right |
| `demo_data.py` | **FAKE** SPHEREx-like frames for trying the app while IRSA is down (same `find_images` / `probe_frames` / `download_full` as the engine). An invented mover at the centre (10"/yr, ±15% between epochs, spectrum dips at 1.4/2.3/3.3 um), an asteroid streak in epoch 2, 3 epochs x 12 frames, half rotated 180 deg. The app shows a red DEMO banner. |
| `spherex_core.py` | Engine: coordinate parsing, IRSA TAP query, probe cutouts for wavelength, parallel cached downloads (`cache/`), frame choice, reprojection to north-up, epoch stacks, GIF, difference image, Gaia DR3 overlay (ESA archive, ARI mirror as backup), target matching, star details |
| `irsa_health.py` | Times four IRSA steps separately, each with a time limit |
| `requirements.txt` | **Pinned** tested versions: streamlit 1.67.0, astropy 8.0.2, astroquery 0.4.11, reproject 0.21.0, numpy 2.5.4, pillow 13.0.0, pandas 3.1.1, pyvo 1.10.0, altair 6.3.0 |
| `README.md` | Public project description, NASA data, ESA (Space Agency Partner) data, all references with licenses, acknowledgements, **AI/SI disclosure**, copyright check. Keep it up to date. |
| `.streamlit/config.toml` | **Theme** (15 Nov ~17:45): dark base, page `#030164` navy, sidebar `#363199` indigo, borders `#2D7495` steel blue, accent/buttons `#E8E085` yellow, text `#F3F4FB`, chart colours from the same palette, Atkinson Hyperlegible / Bricolage Grotesque fonts, minimal toolbar. **Must be uploaded with the app** (Streamlit Cloud reads it from the repo). |
| `assets/logo.png`, `assets/favicon.png` | **Team logo** (compass star, yellow-orange rays, teal; supplied by the first author 15 Nov). 512 px for the header, 64 px browser-tab icon. **Must be uploaded with the app.** |
| `.gitignore` | Ignores `cache/`, `__pycache__/`, `.venv/`, `venv/`, old script outputs |
| `tests/smoke.py` | Offline test (no network): photometry on a fake star, demo spectrum dip, colour picture tint, auto-retry (fake 502s; real errors not retried), the full app in demo mode for both modes (all tabs, spectrum chart, colour pictures), proper motion of WISE 0855 and Barnard's Star. **10/10 pass** (15 Nov ~17:00). |

## Challenge considerations: status (graded 15 Nov ~15:00)
Tiers: **Amazing** (done, and a strength to show) / **Passes** (meets the bar) / **Half way** (part done) / **Not touched**. Nothing below has been checked on real SPHEREx data yet, so treat every tier as "if the real-data run works".

| # | Consideration (from the brief) | Tier | What exists / what's missing |
|---|---|---|---|
| 1 | Easy to update with new data releases | Passes | Each fetch asks IRSA live (search results kept 15 min), so new releases appear on their own. Missing: the release dropdown is hard-coded (`All/dr1/qr3`) and the qr3 URL parsing is unverified. Building the list from the releases actually found would future-proof it. |
| 2 | Creative display of the 102 bands | Passes (demo only) | Detector choice, wavelength matching, wavelength on every frame, the Spectrum tab, and the **Colour tab** (15 Nov ~17:00): a false-colour picture per epoch from 3 wavelength groups of one detector. Not yet run on real data. |
| 3 | Add together each detector's data for S/N | Passes | Epoch stacks (mean/median) per detector. The 45-day gap is still a guess. |
| 4 | Spectra on the fly (extra kudos) | Passes (demo only) | **Spectrum tab added 15 Nov** (see below). Works on demo data; **not yet run on real SPHEREx data**. |
| 5 | Add or subtract images from different times | Amazing | Compare tab: B-A difference (red/blue, robust noise stretch) and A+B sum, works on frames or epoch stacks. |
| 6 | User-chosen field of view | Passes | Slider 0.02-0.20 deg, URL `fov=`. Missing: an estimate of download time; the 0.20 cap limits fast movers. |
| 7 | Overlay Gaia / WISE | **Amazing** | Gaia DR3 **and CatWISE2020 (WISE)** overlays, centre-source identification for both, details panels, and a "seen by WISE but not by Gaia" candidate note (see the CatWISE section). |
| 8 | Coordinates in the URL (embedding) | Passes | `?ra=&dec=&fov=` (plus `gaia=1`) opens and auto-fetches; the URL updates after each fetch. Only useful for embedding once the app is deployed publicly. |
| 9 | Metadata such as contamination flags | Passes | Frames tab metadata, plus **WISE contamination flags** (cc_flags) in the WISE details/table and grey markers for flagged sources. Still missing: SPHEREx's own FLAGS layer. |
| 10 | Use public tools (Astropy, Aladin) | Passes | Astropy, astroquery (IRSA, Gaia), pyvo, reproject. Aladin Lite not used; it would be an easy, visible win (an embedded sky view at the same RA/Dec). |

## Gaia DR3 overlay (15 Nov ~15:30; replaced the earlier MAST/DR2 version)
- **Gaia is a star catalogue, not images.** It can't be blinked; it's shown as markers on the SPHEREx pictures plus a details panel. (MAST was dropped because it only serves Gaia DR1/DR2.)
- Sidebar, "Which data": **Mission data** = "SPHEREx only" / "SPHEREx + Gaia DR3 stars" (inside the form, so it applies on Fetch; `?gaia=1` in the URL selects it).
- `find_gaia()` queries `gaiadr3.gaia_source` (TOP 2000 by G, radius 0.75 x FOV). It tries the **ESA archive via `astroquery.gaia`** first (15 s limit). If that fails it uses the **ARI Heidelberg TAP mirror** (`https://gaia.ari.uni-heidelberg.de/tap`, via pyvo, which comes with astroquery) and skips ESA for 10 min. On 15 Nov **ESA was unreachable from this network** (TCP timeout to gea.esac.esa.int), so every test so far went through ARI. The ESA path itself is **untested**. While ESA is down, the first Gaia query takes ~17 s (15 s ESA wait plus ARI).
- DR3 positions are for epoch 2016.0. `gaia_at()` moves stars in a straight line to each frame's date.
- **Target**: `nearest_gaia()` picks the Gaia star within **4"** of the view centre at the frames' mean date (12" wrongly matched an unrelated G=16 star at WISE 0855). App: a star picker (target first, then the brightest 300), a magenta box on the chosen star in every tab, and an expanded **Details** panel (`gaia_details()`: source id, G, BP-RP, distance from parallax if parallax/error >= 5, proper motion, RV, Teff GSP-Phot, RUWE). If no star matches, an info box says it's a **candidate** worth a look (expected for WISE 0855). Also: a G-limit slider (default 16), the Gaia table in the Frame list tab, and a footer credit.
- SIMBAD link in the Details panel: **unverified**. On 15 Nov every CDS web page returned 404, even the SIMBAD home page. Re-check before the demo.
- **Tested (Python 3.12 venv, real ARI data):** Barnard's Star 1388 stars, target at 0.00", 1.8 pc, PM matches; Eps Ind B target at 0.06" (BP-RP 5.08, 3.7 pc); WISE 0855 gives no match (nearest 8"), as expected. Overlay checked by eye on a synthetic frame. AppTest with a fake result: both mission options, picking another star, the WISE no-match box, no errors. **Not tested:** real SPHEREx frames, a browser, the ESA path.

## Spectrum on the fly (15 Nov ~16:15, `spherex_core.py` + `app.py`)
- `measure_target()`: the target's brightness on the **original** pixels (never the reprojected display copies): sum of 3x3 pixels at the target minus the median of a ring 3-6 px away; error from the ring's robust scatter. It runs inside `_download_cutout`, so every probe and every full frame gets `flux`, `flux_err`, `flux_unit` (header `BUNIT`, not converted) and `bandwidth` (from WCS-WAVE).
- **The probe size went from 0.01 to 0.02 deg** (~12x12 px), so there's room for the background ring. The probe cache key is now `probe2:` (old probes are re-fetched). Older full-frame cache files without flux load with NaN.
- Matched mode: the spectrum uses **every probed candidate** (one point per frame across the detector band), not just the frames in the blink. Epoch mode: every downloaded frame.
- Spectrum tab: dots coloured by epoch (shows variability), ±1σ bars, a grey median line in 17 bins per detector (102/6), tooltips, a CSV download, and "quick-look / candidate" wording.
- Known limits (stated in the app): one detector at a time (six would mean six IRSA searches); a neighbouring star within ~10" blends in; a target moving faster than ~10"/yr drifts out of the box between epochs (the demo mover at 25"/yr ruined its spectrum, so it was slowed to 10"/yr). The box holds ~89% of a point source, so values are about 0.75-0.9x the total.
- **Tested on demo data only:** the measurement recovers a fake star (87.7 vs ~89 expected) and the 1.4 um dip; the app runs in both modes with the chart. **Not tested:** real SPHEREx frames (is `BUNIT` set? is HDU 1 background-subtracted?), and a browser.

## Colour tab (15 Nov ~17:00, `spherex_core.py` + `app.py`)
- `colour_image()`: **Epoch stacks mode only** (it needs every frame; matched mode shows an info box). The detector band is cut into fixed thirds (same for every epoch): shortest = blue, middle = green, longest = red. Each third's aligned frames are averaged (background removed). Each colour is scaled by its own 99.5th-percentile (bright-star) level, so ordinary stars look ~white. Then a colour-preserving asinh stretch (**Lupton et al. 2004**, add to References) keeps the tint of bright objects. (A plain per-channel stretch turned every bright object pure white.)
- Made from the display copies: for spotting, not measuring. Gaia markers work on it.
- Tested on demo data: the demo mover comes out magenta in D2 (its fake 1.4 um dip falls in the green third) and blue in D3 (2.3 um dip in the red third); stars stay ~white. **Side effect worth showing:** an asteroid within one epoch appears as separate red/green/blue dots, because each third was observed at a different time. Groups with 1-2 frames give speckled colour noise.

## Auto-retry for IRSA's search (15 Nov ~17:00)
- `find_images()` retries up to 3 tries in total (waits 3 s, 8 s) **only** on "busy" answers (502/503/504, Proxy Error, Service Unavailable, Gateway; see `is_busy_error`), always within the 60 s TAP limit. Timeouts and real errors are not retried. The app's error message now says "IRSA is overloaded ... tried 3 times" and points to demo mode.
- Tested: in the smoke test (fake 502s, then success; a real error is not retried), and **live against IRSA** at ~17:00: it retried at 1.0 s and 4.2 s and gave up at 12.5 s on a 502, recognised as busy.

## Frontend redesign (15 Nov ~17:45, `app.py` + `.streamlit/config.toml`)
- The theme lives in `config.toml`; `inject_css()` in `app.py` adds the Google fonts, a gradient **hero header** with feature pills, yellow section labels in the sidebar ("1 · Where to look", "2 · Which data", "3 · How to build the animation"), **collapsible expanders** for the matched and epoch settings, a full-width yellow Fetch button (dark text for contrast), a **3-step welcome** on the empty page, **summary tiles** (custom HTML `tile()`, because `st.metric` draws misleading up-arrows), a styled footer, and a light-blue median line on the spectrum. The demo banner is now `st.warning` (yellow) instead of `st.error` (red); the smoke test was updated.
- All error and status messages were reworded to be neutral (see "How to work with this team").
- **Tested:** the smoke test 10/10, plus **real browser screenshots** (Playwright driving the installed Edge, scratch venv only, not a project dependency): the landing page, the demo results page (tiles, blink GIF), Spectrum and Colour tabs all render correctly in the new colours. Fonts load from Google Fonts; offline, the browser falls back to Segoe UI.

## Redesign v2: sleek, large stage, mobile (15 Nov ~18:30; replaces the v1 look above)
- Made with the `frontend-design` skill. v1's generic tells were removed: gradient hero, pills, ALL-CAPS "1 · WHERE TO LOOK" labels with middle dots, four identical tiles.
- **Palette:** navy `#030164` page, indigo `#363199` sidebar, steel `#2D7495` lines, yellow `#E8E085` only for things to act on or notice (Fetch, active tab, step numerals); plus darkroom `#01003A` behind pictures and pale sky `#C9E4F2` for secondary text. **Type:** Bricolage Grotesque (wordmark, headings, tabs) + Atkinson Hyperlegible (body). Fonts come from Google Fonts; offline the browser falls back to Segoe UI.
- **Landing:** a headline ("See what moves in the infrared sky."), a 3-step ordered list, and a **live demo blink** (`landing_blink()`, cached, built from `demo_data`) as the hero. On phones a hint points to the controls arrow.
- **Results:** a slim header (`mast_slot`, placed at the top so it sits above the status box), a short demo banner, **one readout strip** (`readout()`), then tabs **Blink / Step through / Compare / Spectrum / Colour / Frames**. Every picture tab uses a **5:2 stage**: picture left, controls right; columns stack on phones. Pictures render at `core.STAGE_PX = 960` px with labels scaled by `_label_size()`, shown full column width, `image-rendering: pixelated`, on a darkroom panel. The Colour tab is now one large picture with an epoch picker and colour swatches (the smoke test expects >= 1 image).
- **Sizes measured in Edge (Playwright, scratch venv):** desktop 1440x900: blink **737 px = 51% of screen width, 42% of screen area**; phone 390x844: blink **364 px = 93% of width, 40% of area**. The phone wordmark sits below Streamlit's top bar (mobile top padding 3.6rem).
- Also: visible yellow keyboard focus, `toolbarMode = "minimal"` in config.toml. Smoke test 10/10. **Not checked:** a real phone, real SPHEREx data in the new layout.

## CatWISE2020 (WISE) overlay (15 Nov ~19:15, `spherex_core.py` + `app.py`)
- **Why:** brief item #7 says "Gaia or WISE". WISE observed at 3.4 um (W1) and 4.6 um (W2), the same wavelengths as SPHEREx D4-D6, and sees the cold, red objects Gaia misses. CatWISE2020 (WISE + NEOWISE) includes proper motions.
- **Source:** NOIRLab **Astro Data Lab** TAP (`https://datalab.noirlab.edu/tap`, table `catwise2020.main`, `q3c_radial_query`), via pyvo, about 11-17 s per query, cached for an hour. IRSA's own copy (`catwise_2020`) was **untested**: IRSA's TAP returned 502 on every query at ~19:00. Positions `ra_pm/dec_pm` are for MJD 57170 = **2015.405**; `pmra/pmdec` are in **arcsec/yr** (converted to mas/yr). A motion is used only if it is >= 3 sigma (faint-source motions are noisy).
- `find_wise()`, `wise_details()` (W1, W2, W1-W2 with a plain-language hint, motion, cc_flags), `add_wise_markers()` (diamonds: lavender, orange = fast, **grey = contamination flags D/P/H/O**, magenta = centre source). The centre match reuses `nearest_gaia()` (4"). App: the sidebar multiselect "Star catalogues to overlay"; a **Catalogues tab** holds the Gaia and WISE panels (moved out of the way so the picture stays near the top); markers from both catalogues are drawn on every picture tab (`decorate_all`). If Gaia has no centre star but WISE does, a green note says "Seen by WISE but not by Gaia ... **candidate**".
- **Tested live:** WISE 0855 is found as **CWISE J085506.91-071437.5**, 1.2" from the app's 2026 position, W1 19.46, W2 15.54, **W1-W2 = 3.91**, motion 7935 mas/yr (Kirkpatrick+2021: ~8124). AppTest at WISE 0855 with both catalogues: the WISE-but-not-Gaia note appears, no errors. Browser screenshot: markers render.
- **Known limits (stated in the app):** Barnard's Star saturates WISE (only flagged "H" halo fragments with wrong motions nearby); Epsilon Indi B sits near its very bright primary and its CatWISE motion (0.90, -0.68 "/yr) disagrees with Gaia's (3.98, -2.47), so it is not matched. **Use Gaia for bright stars, WISE for faint cold ones.**
- References to add: CatWISE2020 (Marocco et al. 2021), WISE/NEOWISE (NASA/JPL/IPAC), NOIRLab Astro Data Lab.

## Guidebook (15 Nov ~19:15, `app.py`)
- A yellow **Guide** button fixed bottom right on every page (`guidebook()`, called right after `inject_css()`); it opens a glossary panel above it. Plain HTML `<details>`, so no JavaScript and keyboard-accessible. Content is `GLOSSARY` (4 groups: the sky, SPHEREx data, reading the pictures, other catalogues; about 26 terms, plain language, "candidate" explained). The panel is positioned above the button so they never overlap (measured: desktop panel ends 11 px above the button, phone 10 px).

## UI review and cleanup (15 Nov ~19:45, `ui-ux-pro-max` skill; `app.py`, `config.toml`, `tests/smoke.py`)
- **Measured contrast (WCAG):** text on navy 16.1:1, on indigo 9.3:1; pale sky on navy 13.3:1; yellow on navy 12.9:1; navy on the yellow button 12.9:1. **Two failures fixed:** grey captions on the indigo sidebar were 4.4:1 (< 4.5) and are now pale sky (7.7:1); steel field borders on indigo were 2.0:1 (< 3:1 for controls) and are now light blue `#6FB3D9` (4.4:1) on a deeper field well `#221E85` (`[theme.sidebar]` in config.toml).
- **Less clutter:** the fetch status box is cleared after a successful fetch (`status_slot`; errors stay visible); the heavy olive alert boxes became slim notices with a coloured left edge (`show_notice()`: yellow = demo, amber = warning); the epoch-stack explanation moved from a box above the tabs into the note beside the picture. Result: the picture starts 80 px higher on desktop (y = 348 of 900).
- **Consistency:** one wordmark, in the page header on every page (the landing page too); the sidebar copy was removed. Notes beside pictures are 0.9 rem in full pale sky.
- **Phones:** the Guide button is compact (96x44 px, meets the 44 px touch target) and the page has extra bottom padding, so content can be scrolled clear of it.
- The smoke test now looks for the demo notice in markdown (it is no longer `st.warning`). 10/10. Checked in Edge at 1440x900 and 390x844.

## Wording trim, STE guidebook, background gradient (15 Nov ~20:15, `app.py`)
- **Wording:** 39 user-facing texts cut down (help tooltips, captions, notices, landing text, footer). Kept every honesty point: DEMO MODE label, "quick-look only", "candidate", the credits. Keep new text this short: one idea per caption.
- **Guidebook (`GLOSSARY`)** rewritten in **ASD-STE100 Simplified Technical English at about half strength**: sentences of 20 words or fewer (longest is 15; 76 sentences), one idea per sentence, active voice, present tense, no idioms, one word per meaning. Technical names (parallax, W1, RUWE) stay. Group "Reading the pictures" is now "Pictures"; "Finding your way on the sky" is now "Position on the sky".
- **Background:** a vertical gradient `BG_TOP #070979` -> navy `#030164` (45%) -> `BG_BOTTOM #02013E`, fixed, with the top bar transparent. The top colour was chosen so steel borders keep 3:1 (3.05:1; a brighter `#0A0C86` gave 2.9:1). Text contrast 14.5:1 at the top, 17.8:1 at the bottom.
- Smoke test 10/10; checked in Edge at 1440x900.

## Landing hero and wordmark (15 Nov ~20:45, `app.py`)
- The landing intro text and 3 steps are inside a collapsible **"How it works"** (`<details class="sc-howto">`, closed by default). Smaller headline; columns 1:2, so the demo blink is **685 px** on desktop (was 566).
- **Wordmark** `.sc-word` (landing and results header): **Syne 800** (Google Fonts, used only here), yellow `#E8E085` with a soft glow, and a four-point star before it that twinkles (CSS `sc-twinkle`; off when the system asks for reduced motion).
- `requirements.txt` is now **pinned** to the tested versions (plus pyvo, altair) and `.gitignore` excludes `.claude/`, `.vscode/`, `skills-lock.json`. A fresh-venv install check of the pinned list was started but **interrupted; rerun it before deploying.**
- Smoke test 10/10; checked in Edge at 1440x900 and 390x844.

## Logo, wordmark font, landing layout (15 Nov ~21:15, `app.py`, `assets/`)
- **Logo** (`assets/logo.png`, cropped from the supplied 1280 px PNG, already transparent): shown before "Scintillation" in the header on every page (`wordmark()`, inlined as base64 via `logo_data_uri()`), and as the browser-tab icon (`page_icon=assets/favicon.png`). It twinkles gently (CSS `sc-twinkle`; off with reduced motion).
- **Wordmark font: Chakra Petch 700** (Google Fonts), chosen from 5 candidates rendered next to the logo (Chakra Petch, Michroma, Oxanium, Syncopate, Audiowide): its cut corners echo the logo's rays. Filled with the logo's yellow-to-orange gradient (`#FFE600 -> #FFC400 -> #FFA000`). Syne is no longer used.
- **Landing:** the picture is first, next to the sidebar (columns 5:2, **737 px, 42% of a 1440x900 screen**); the headline, text and steps fold into **"About this tool"** beside it (closed by default). On phones the "tap the arrow" hint sits above the picture.
- Smoke test 10/10; checked in Edge at 1440x900 and 390x844.

## Credits, AI/SI disclosure, copyright check (15 Nov ~21:45)
- **`README.md`** written: features, how to run, NASA data (SPHEREx via IRSA, IRSA tutorial, WISE/NEOWISE, NASA Exoplanet Archive, the challenge brief), **Space Agency Partner data: ESA Gaia DR3**, other services (CatWISE2020 via NOIRLab Astro Data Lab, ARI Heidelberg mirror, CDS Sesame/SIMBAD), libraries with licenses (read from package metadata), fonts (all SIL OFL, checked in the ui-ux-pro-max font license data), papers, standard acknowledgement texts, AI use, copyright check, limitations.
- **AI/SI facts from the first author:** the logo was **drawn by the team** (no AI, no watermark needed); the first version and the friend's work **also used Claude**. Only Claude (via Claude Code) was used.
- **SI labelling in the app:** every Python file has an AI-use note at the top; the footer has an "AI use" paragraph; **every demo picture carries a visible watermark** "SYNTHETIC DEMO DATA, made with AI-assisted code" (`core.add_watermark`, applied through `decorate` in demo mode and on the landing blink). Real SPHEREx pictures are not watermarked.
- **Copyright:** IRSA tutorial is BSD-3-Clause (c) 2022-2026 Caltech (checked on GitHub); all libraries are Apache/BSD/MIT-CMU; no NASA/agency logos or insignia anywhere; mission names appear only as data credit text.
- If a demo video uses any AI-generated images/voice, it must carry a visible SI watermark too.

## Data facts
- Data come from NASA/IPAC **IRSA** via `astroquery.ipac.irsa`, following the IRSA tutorial https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts (credit it in References).
- A TAP query on `spherex.artifact` JOIN `spherex.plane` (CONTAINS on `p.poly`, `energy_bandpassname = 'SPHEREx-D#'`) gives cutout URLs of the form `...?center=RA,DECd&size=DEG`. HDU 1 is the IMAGE. The wavelength at a pixel comes from the WCS with `key="W"` (the WCS-WAVE extension).
- Wavelength is **not** in the search table. The app downloads tiny 0.02 deg "probe" cutouts to read it (and the target's brightness, for the spectrum), then full cutouts only for the chosen frames.
- Releases: DR1 = surveys 1+2 (reprocessed); QR3 = surveys 3+4, released weekly since Sep 2026. At WISE 0855, an earlier run found 48 D2 images, all dr1. The release name is parsed from the URL path after `/spherex/`, which is unverified for qr3.
- Detectors (um): D1 0.75-1.09, D2 1.10-1.62, D3 1.63-2.41, D4 2.42-3.82, D5 3.83-4.41, D6 4.42-5.00. Wavelength varies along each detector. Pixels are about 6.2 arcsec.
- Frames come in different orientations (some rotated 180 deg), so all are reprojected to one north-up grid. These are **display copies only: never measure brightness from them.** Cutouts are downloaded 1.45x the view size so rotated frames leave no empty corners.
- Time limits: TAP 60 s, per file 60 s, probes 90 s total, downloads 240 s total, one retry pass 60 s. IRSA has been slow before (503 errors, one 15-minute hang). On 15 Nov ~15:40, IRSA's TAP failed three times in a row on the real SPHEREx search (a 60 s timeout, then 502 Proxy Error, then 503), while a tiny TAP_SCHEMA query still worked. That points to IRSA's SPHEREx search backend being overloaded, not to the app.

## Design decisions (and why)
- Streamlit/Python, because the team is new and the astronomy tools are Python.
- At least 10 days between frames by default, because many frames are taken within hours of each other and show no motion.
- Wavelength matching is on by default, because mixing colours makes stars flicker. Turning it off shows a warning banner.
- Epoch stacks: mean or median per epoch. **The default epoch gap of 45 days is a guess**; check it against real frame lists.

## Proper-motion change (designed by the friend's Claude; **applied in this copy** 15 Nov ~14:20)
No `.patch` file arrived here, so this Claude wrote the change into `app.py` from the description below (`DISPLAY_EPOCH`, a `PRESETS` dict with lookup/fallback coordinates/PM/distance, `preset_position()`, the sidebar note, the new default RA/Dec). If the friend's version shows up on GitHub, compare the two before merging, because they were written separately.

`app.py` quick targets now move fast stars forward to `DISPLAY_EPOCH = 2026.0` with `SkyCoord.apply_space_motion`:
- **Barnard's Star**: the J2000 position is about 270" from where the star is now, which is outside the default field (half-width 180"). Gaia DR3 PM is (-801.551, +10362.394) mas/yr.
- **WISE 0855**: the brief's coordinates are from 2010 WISE data, and it has moved about 126" west since. PM is (-8123.7, +673.2) mas/yr (Kirkpatrick+2021). The default RA/Dec is now the moved position (133.760098, -7.242266).
- **Epsilon Indi B**: the old preset looked up "eps Ind", which is star A, about 402" from B, so B was out of frame. It now looks up "eps Ind B", falling back to (331.043833, -56.782708) from the NASA Exoplanet Archive. Since 15 Nov ~15:30 it uses **B's own Gaia DR3 PM** (+3982.0, -2466.8) mas/yr and 3.7 pc (source 6412596012146801152), instead of A's. The preset now lands 0.06" from Gaia (it was 1.8" with A's PM).
- **Tested here (Python 3.12 venv):** real SIMBAD lookups for all five targets work, and "eps Ind B" agrees with the fallback to within about 1". The moves come out at 270" (Barnard) and 126" (WISE 0855), and WISE 0855 lands exactly on 133.760098, -7.242266 (using the WISE epoch 2010.6). Streamlit AppTest: the page loads and every quick target fills RA/Dec without errors. **Not yet tested:** real IRSA data.
- `irsa_health.py` still uses the 2010 WISE 0855 position. That's fine, because it only times IRSA.

## Science expectations (important for honesty and Validity)
- In 6 months Barnard's Star moves about 5" and WISE 0855 about 4", which is **less than one pixel**. A DR1-only blink will barely show motion. For the demo, use "All releases" (widest time span) and the **Compare difference image**, where a mover shows as a red/blue pair.
- WISE 0855 has H of about 23.8 mag and is extremely cold, so it is almost certainly invisible in D1-D4. Only D5/D6 epoch stacks have any chance. If it isn't seen, present that honestly as a result.
- Expect only about 2-3 epochs per detector at one position, so animations will be short.

## Next steps, in priority order
1. **Real-data run.** Run `irsa_health.py`, then the app on each test target. Fix whatever breaks. Check: GIF animates in the browser; qr3 URLs parse correctly; the 45-day epoch gap matches real clusters; WISE 0855 in D5/D6.
2. **Deploy publicly on Streamlit Community Cloud** from the GitHub repo. Its current requirements are unverified, so check its docs (Python version selection, whether `requirements.txt` needs pinning). Pin versions that work locally. These installed together and passed the checks above on Python 3.12: streamlit 1.67.0, astropy 8.0.2, reproject 0.21.0, astroquery 0.4.11 (the friend's sandbox reported the same first three on Python 3.13). Remember `cache/` is temporary on the cloud.
3. **Fallback bundle**: pre-downloaded frames for the test targets, so the demo works if IRSA is down.
4. **Frontend polish**: theme, layout and neutral wording **done 15 Nov ~17:45** (see "Frontend redesign"). The first author may still fine-tune it.
5. Optional, by value (see the considerations table): Aladin Lite sky view (#10, quick), spectra on the fly (#4, extra kudos), contamination flags (#9), a releases list built from the data (#1), a WISE overlay (#7).
6. **Submission**: References (SPHEREx/IRSA, IRSA tutorial, Astropy, astroquery, reproject, NumPy, Pillow, pandas, Streamlit, pyvo, ESA Gaia DR3 (ESA archive and the ARI Heidelberg mirror) for the overlay and proper motions, Kirkpatrick+2021 for WISE 0855's PM, SIMBAD/CDS, Lupton et al. 2004 for the colour stretch, CatWISE2020 (Marocco et al. 2021) via NOIRLab Astro Data Lab, WISE/NEOWISE), demo video, honest limitations, "candidate" wording.

## Links
- Challenge: https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/ (blocks automated fetching; ask the team for the text)
- Judging guide: https://www.spaceappschallenge.org/resources/judging-awards-guide/

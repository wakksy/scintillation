# Scintillation

**See what moves in the infrared sky.** Scintillation is a public web tool built for the
**NASA Space Apps Challenge 2026**, challenge **"Planet X and SPHEREx"**. You enter a sky
position (RA/Dec), and the tool shows a series of NASA SPHEREx images of that patch,
played like a short video ("blink"), so that moving or changing objects are easy to see.

> Results from this tool are **candidates**, not discoveries. Pictures are resampled for
> display and are not suitable for measuring brightness. Every candidate needs expert
> follow-up.

Live app: *(add the Streamlit Community Cloud link here after deployment)*

---

## What the tool does

| Feature | What it shows |
|---|---|
| **Blink** | SPHEREx frames of one sky patch, oldest first, north up. Objects that move jump between frames. |
| **Step through** | The same frames, one at a time, with a slider. |
| **Compare** | Difference (B − A) or sum (A + B) of two frames or epochs. A red-blue pair marks something that moved. |
| **Epoch stacks** | All frames of one observing period added together, per detector, for a deeper picture. |
| **Spectrum** | Brightness of the object at the centre against wavelength, from every frame (quick-look photometry). CSV download. |
| **Colour** | False-colour picture: short, middle and long wavelengths of one detector shown as blue, green and red. |
| **Catalogues** | Optional ESA Gaia DR3 and CatWISE2020 (WISE) markers, moved to each frame's date, with details of the source at the centre and WISE contamination flags. |
| **Demo data** | Synthetic, watermarked SPHEREx-like frames, so every feature can be tried offline. Not real observations. |
| **Shareable links** | `?ra=..&dec=..&fov=..` (plus `&gaia=1`, `&wise=1`, `&demo=1`) opens the app at a position. |
| **Guide** | A glossary of terms (button bottom right), written in Simplified Technical English. |

## Run it on your computer

Requires Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Offline test (no network, uses the synthetic demo data): `python -I tests/smoke.py .`

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit web page. |
| `spherex_core.py` | Data search, downloads, alignment, stacks, spectra, colour pictures, catalogue overlays. |
| `demo_data.py` | Synthetic demo frames (not real data). |
| `irsa_health.py` | Times each step of the IRSA data service. |
| `tests/smoke.py` | Offline test. |
| `assets/` | The team's logo and browser-tab icon. |
| `.streamlit/config.toml` | Colour theme and fonts. |
| `requirements.txt` | Python libraries, pinned to the tested versions. |

---

## NASA data and resources used

| NASA data / resource | How we use it |
|---|---|
| **SPHEREx** spectral images (NASA mission; data releases DR1 and QR3) | The core data: every blink, stack, difference image, spectrum and colour picture is made from SPHEREx image cutouts. |
| **NASA/IPAC Infrared Science Archive (IRSA)** | Hosts the SPHEREx data. We search it with its TAP service (`spherex.artifact`, `spherex.plane`) and download image cutouts from it, through `astroquery.ipac.irsa`. |
| **IRSA tutorial "Download a collection of SPHEREx Spectral Image cutouts"** (https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts) | Our search and cutout method is adapted from it (BSD 3-Clause, © 2022-2026 California Institute of Technology). |
| **WISE and NEOWISE** (NASA missions) | Source of the CatWISE2020 catalogue used for the infrared overlay. |
| **NASA Exoplanet Archive** | Stored fallback position for Epsilon Indi B, if the name lookup is unavailable. |
| **NASA Space Apps 2026 challenge brief** "Planet X and SPHEREx" | The task, the test targets (Barnard's Star, Epsilon Indi B, WISE 0855, RW Aurigae, R Draconis) and the design considerations. |

## Space Agency Partner data

| Partner | Data | How we use it |
|---|---|---|
| **ESA (European Space Agency)** | **Gaia DR3** star catalogue (positions, proper motions, parallaxes, photometry) | Optional overlay that marks and names stars, identifies the star at the centre, and shows its distance, motion, colour and temperature. Proper motions from Gaia DR3 also move Barnard's Star and Epsilon Indi B to their 2026 positions. Read from the ESA Gaia Archive, with the ARI Heidelberg Gaia mirror as a backup. |

## Other data and services

| Resource | Use |
|---|---|
| **CatWISE2020** catalogue, served by **NSF NOIRLab Astro Data Lab** (`catwise2020.main`) | Infrared overlay (W1, W2, colour, proper motion, contamination flags). |
| **ARI Heidelberg Gaia TAP mirror** (Astronomisches Rechen-Institut, Heidelberg University) | Backup source for Gaia DR3. |
| **CDS Sesame / SIMBAD** (Strasbourg astronomical Data Center) | Name lookup for the quick targets (through Astropy), and a link to SIMBAD in the star details. |

## Software and libraries

All are open source. Licenses were read from the installed package metadata.

| Software | Version | License | Use |
|---|---|---|---|
| Python | 3.12 | PSF License | Language |
| Streamlit | 1.67.0 | Apache-2.0 | Web app framework |
| Astropy | 8.0.2 | BSD-3-Clause | Coordinates, FITS files, WCS, time |
| astroquery | 0.4.11 | BSD-3-Clause | IRSA and ESA Gaia queries |
| reproject | 0.21.0 | BSD-3-Clause | Aligning frames to one north-up grid |
| PyVO | 1.10.0 | BSD-3-Clause | TAP queries (ARI Gaia mirror, Astro Data Lab) |
| NumPy | 2.5.4 | BSD-3-Clause (with bundled MIT, Zlib, 0BSD, CC0 parts) | Arrays and maths |
| pandas | 3.1.1 | BSD-3-Clause | Tables |
| Pillow | 13.0.0 | MIT-CMU | Pictures and animated GIFs |
| Vega-Altair | 6.3.0 | BSD-3-Clause | Spectrum chart |

### Fonts

Loaded from Google Fonts at run time (not copied into this repository). All are licensed
under the **SIL Open Font License (OFL)**.

| Font | Designer | Use |
|---|---|---|
| Chakra Petch | Cadson Demak | "Scintillation" wordmark |
| Bricolage Grotesque | Mathieu Triay | Headings and tabs |
| Atkinson Hyperlegible | Braille Institute and Applied Design Works | Body text |

### Logo

The compass-star logo (`assets/logo.png`, `assets/favicon.png`) was **drawn by the team**.
It is not AI-generated and does not use any NASA or agency logo or design element.

### Writing and accessibility standards

- **ASD-STE100 Simplified Technical English** (applied at about half strength to the Guide
  text: short sentences, one idea each, active voice).
- **WCAG 2** colour-contrast guidance (text at least 4.5:1, controls at least 3:1; contrast
  was measured for the colour scheme).

## Scientific references

- Doré, O. et al. 2014, *Cosmology with the SPHEREX All-Sky Spectral Survey*, arXiv:1412.4872
- Gaia Collaboration, Prusti, T. et al. 2016, A&A 595, A1 (the Gaia mission)
- Gaia Collaboration, Vallenari, A. et al. 2023, A&A 674, A1 (Gaia Data Release 3)
- Wright, E. L. et al. 2010, AJ 140, 1868 (WISE)
- Mainzer, A. et al. 2011, ApJ 731, 53 (NEOWISE)
- Marocco, F. et al. 2021, ApJS 253, 8 (CatWISE2020)
- Kirkpatrick, J. D. et al. 2021, ApJS 253, 7 (proper motion of WISE 0855 used for the preset)
- Luhman, K. L. 2014, ApJL 786, L18 (discovery of WISE 0855)
- Lupton, R. et al. 2004, PASP 116, 133 (colour-preserving asinh stretch for the Colour view)
- Wenger, M. et al. 2000, A&AS 143, 9 (SIMBAD)
- Akeson, R. L. et al. 2013, PASP 125, 989 (NASA Exoplanet Archive)
- Astropy Collaboration 2013, A&A 558, A33; 2018, AJ 156, 123; 2022, ApJ 935, 167
- Ginsburg, A. et al. 2019, AJ 157, 98 (astroquery)
- Harris, C. R. et al. 2020, Nature 585, 357 (NumPy)
- VanderPlas, J. et al. 2018, JOSS 3(32), 1057 (Altair)

## Acknowledgements

- This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by
  the National Aeronautics and Space Administration and operated by the California
  Institute of Technology.
- This work uses data from the NASA SPHEREx mission, obtained from IRSA.
- This work has made use of data from the European Space Agency (ESA) mission Gaia
  (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis
  Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the
  DPAC has been provided by national institutions, in particular the institutions
  participating in the Gaia Multilateral Agreement.
- This publication makes use of data products from the Wide-field Infrared Survey
  Explorer, which is a joint project of the University of California, Los Angeles, and the
  Jet Propulsion Laboratory/California Institute of Technology, funded by the National
  Aeronautics and Space Administration; and from NEOWISE, a project of the Jet Propulsion
  Laboratory/California Institute of Technology, funded by NASA.
- This research uses services and data provided by the Astro Data Lab of NSF NOIRLab
  (CatWISE2020 catalogue access).
- Gaia DR3 backup access through the Gaia TAP service of the Astronomisches Rechen-Institut,
  Zentrum für Astronomie, Heidelberg University.
- This research has made use of the SIMBAD database and the Sesame name resolver, operated
  at CDS, Strasbourg, France.

*Team note: check the exact acknowledgement wording on each provider's website before
publishing a paper; the texts above are for this hackathon project.*

---

## AI / SI (Super Intelligence) use

We used one AI tool: **Claude (Anthropic)**, a large language model, through **Claude
Code** (a coding assistant that runs in the terminal and edits files with our approval).
Both team members used Claude.

**Where and how we used it**

| Area | How Claude helped | What the team did |
|---|---|---|
| **Code** (`app.py`, `spherex_core.py`, `demo_data.py`, `irsa_health.py`, `tests/smoke.py`) | Wrote and changed code on our request: data search and downloads, frame alignment, epoch stacks, spectrum and colour views, Gaia and WISE overlays, retries, demo data, tests. | Chose every feature, gave the requirements, ran and checked the app, reviewed the results, and decided what to keep. |
| **Debugging and testing** | Found and fixed bugs, wrote the offline test, ran live checks against the data services, took browser screenshots to check layout. | Reported problems seen in the running app and confirmed fixes. |
| **Design** | Proposed layout, colours, contrast fixes and font choices (using the design skills `frontend-design` and `ui-ux-pro-max` in Claude Code). | Chose the colour palette, gave the design direction, and approved or rejected each proposal. |
| **Text** | Drafted the on-screen text, the Guide glossary, and this README. | Set the wording rules (neutral wording, Simplified Technical English, "candidate" not "discovery") and reviewed the text. |
| **Research** | Checked documentation, data columns, catalogue values and software licenses. | Decided which data sources to use (for example, Gaia DR3 and WISE). |

**What AI was not used for**

- **The idea.** The concept (a blink tool for SPHEREx images to find moving objects)
  comes from the NASA challenge brief and the team's own plan. Claude suggested ways to
  build features; the team chose what to build.
- **The logo.** It was drawn by the team.
- **The data.** All real data shown comes from NASA SPHEREx (IRSA), ESA Gaia and
  CatWISE2020. Claude did not create or change any real measurement.

**Labels and watermarks**

- **Code:** every Python file starts with an AI-use note (its "metadata"), and the app
  footer states that the code and text were written with help from Claude.
- **Images:** the only generated images are the **demo pictures**, made by AI-assisted
  code from invented data. Each one carries the visible watermark
  **"SYNTHETIC DEMO DATA, made with AI-assisted code"**, and the page shows a DEMO MODE
  notice. Real SPHEREx pictures are NASA data and are not watermarked.
- **Audio:** none.
- **Video:** if the demo video uses any AI-generated images, voice or footage, it must
  carry a visible SI watermark and say so in its description.

## Copyright and branding check

- **No NASA or agency logos, insignia, flags or design elements** are used anywhere in the
  app, the logo or the generated pictures. Mission names (SPHEREx, Gaia, WISE) appear only
  as text, to credit the data sources.
- All code libraries and fonts are open source, and are listed above with their licenses.
- The IRSA tutorial method is used under its BSD 3-Clause license, with credit.
- The logo is the team's own work.
- The data are public archive data, credited above.
- No copyrighted images, text or music from other sources are included.

## Limitations

- Pictures are resampled for display; do not use them to measure brightness.
- The spectrum is quick-look photometry in a 3×3-pixel box. Nearby stars and motion faster
  than about 10″/yr affect it.
- Very bright stars saturate WISE; use Gaia for them.
- The image archive can be slow or busy; the app retries and offers demo data meanwhile.
- Anything the tool shows is a **candidate** until experts confirm it.

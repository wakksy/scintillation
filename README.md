# SPHEREx Blink

A public web tool for the NASA Space Apps Challenge 2026, **"Planet X and SPHEREx"**.
Enter a patch of sky (RA and Dec), and the tool shows SPHEREx images of that patch over
time as a blink, a manual slider, an epoch stack, or an add/subtract comparison, so that
moving objects (asteroids, comets, nearby brown dwarfs) are easy to spot.

## Run it on your computer

```
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Then press **Fetch frames** in the sidebar. The first fetch takes a while because cutouts
are downloaded from IRSA; downloaded frames are cached in a local `cache/` folder.

A link such as `?ra=133.795129&dec=-7.245147&fov=0.1` opens the tool already pointed at
that position, so it can be embedded or shared.

## What it does

- RA/Dec input (degrees or hours/degrees), field-of-view slider, quick targets
- **Matched-wavelength mode:** a few frames at similar wavelength, at least N days apart
- **Epoch stacks mode:** all frames of each observing period added together
- Auto-play animation with adjustable time per frame, and a manual frame slider
- **Compare tab:** difference (B minus A) or sum of two frames/epochs
- Always queries IRSA live, so new SPHEREx releases appear automatically

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit web page |
| `spherex_core.py` | Data access, frame selection, alignment, stacking, rendering |
| `irsa_health.py` | Times each IRSA step to see whether IRSA or the app is slow |
| `requirements.txt` | Python packages |

## Limits and honesty

- Pictures are resampled for display and are **not suitable for measuring brightness**.
- Anything that appears to move is a **candidate for review**, not a confirmed discovery.
- Frames of one target are at different wavelengths; matching wavelengths, or stacking
  epochs, is needed for a like-for-like comparison.

## References and credits

- NASA SPHEREx mission data, served by the NASA/IPAC Infrared Science Archive (IRSA)
- IRSA tutorial "Download a collection of SPHEREx Spectral Image cutouts as a
  multi-extension FITS file": https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts
  (the data-access method is adapted from it)
- Astropy, astroquery, reproject, NumPy, Pillow, pandas, Streamlit

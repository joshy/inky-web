# inky-web

A small Flask web interface for a Pimoroni **Inky Impression 13.3" (Spectra 6,
EL133UF1)** picture frame. Upload a photo from your phone, it gets cropped to
the portrait frame and pushed to the e-paper panel. Includes a shutdown button
so the Pi can be powered down safely.

Runs on a Raspberry Pi Zero 2 W as `inky-web.service`, served on port 80.

## Layout

| File                | Purpose                                      |
|---------------------|----------------------------------------------|
| `app.py`            | The Flask app and the Inky update routine    |
| `inky-web.service`  | systemd unit (runs from the `pimoroni` venv) |
| `requirements.txt`  | Python dependencies                          |

## Install

```sh
python3 -m venv ~/.virtualenvs/pimoroni
~/.virtualenvs/pimoroni/bin/pip install -r requirements.txt

sudo cp inky-web.service /etc/systemd/system/
sudo systemctl enable --now inky-web.service
```

`/boot/firmware/config.txt` needs I2C (board detection) and SPI (everything
else), with SPI's hardware chip-select disabled — the 13.3" panel has two
chip-select lines that the driver drives itself via GPIO 26 and GPIO 16:

```
dtparam=i2c_arm=on
dtoverlay=spi0-0cs
```

## Known issue: "updated successfully" but the panel keeps the old image

The symptom is that an upload returns *"Picture updated successfully"* while the
panel still shows the previous photo.

**Cause.** `inky <= 2.4.0` read the panel's BUSY line with inverted polarity.
BUSY on the EL133UF1 is active-**low** (low while the panel is refreshing), but
`_busy_wait()` treated a low reading as "already idle" and returned
immediately. So `_update()` sent `POF` (panel power off) roughly 0.3 s after
`DRF` (refresh trigger). The refresh was killed before it started, and because
nothing raised, the web UI reported success. A correct refresh takes ~25-35 s;
the broken path made `show()` return in ~9 s.

**Fix.** `inky >= 2.6.0`, which adds `_refresh_wait()` — it waits for BUSY to
assert *and then* clear before cutting panel power. This is pinned in
`requirements.txt`.

**Follow-up.** Repeatedly cutting power mid-refresh can latch the panel into a
fault state where it holds BUSY low forever and ignores SPI entirely. A reboot
does **not** clear this, because the HAT keeps its rails from the Pi header.
Physically disconnect power from the Pi for ~30 s.

To tell the two apart, check how long a refresh takes:

```sh
sudo systemctl stop inky-web.service
~/.virtualenvs/pimoroni/bin/python - <<'PY'
import time, warnings
from PIL import Image
from inky.auto import auto
d = auto(verbose=True)
d.set_image(Image.new("RGB", d.resolution, (255, 0, 0)))
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    t = time.time()
    d.show()
    print(f"show() took {time.time() - t:.1f}s")
    for x in w:
        print("WARNING:", x.message)
PY
```

- ~25-35 s, no warnings → healthy.
- A `Refresh Wait: Timed out` warning → BUSY never released; power-cycle the Pi.
- Under ~10 s → an old `inky` is still installed.

`app.py` now turns those warnings into an error, so a stalled refresh is
reported as a failure in the web UI instead of a success.

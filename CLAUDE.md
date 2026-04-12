# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python ctypes wrapper for the ASQE Spectrometer — a UV-Vis spectroscopy device with a 3648-element CCD detector. The library bridges Python user code to a platform-specific native C library (`lib/libspectr.{dll|dylib|so}`).

## Setup

```bash
pip install -r requirements.txt
```

No build step required. The pre-compiled native libraries are in `lib/`.

## Running Examples

```bash
python spectrum.py          # Real-time raw spectrum plot
python spectrum_norm.py     # Normalized intensity (background-subtracted)
python spectrum_calib.py    # Calibrated spectrum with wavelength axis (uW/cm²/nm)
```

## Architecture

### Core Pattern: ctypes Wrapper

```
User Code → ASQESpectrometer (libspec.py) → ctypes bindings → libspectr.{dll|dylib|so} → Device (USB/HID)
```

### `libspec.py` — Main library

The `ASQESpectrometer` class:
- **Constructor**: Detects platform/architecture, loads the correct native library from `lib/`, connects to device via `connectToDevice()`.
- **`set_parameters()` / `configure_acquisition()`**: Set device state variables (`num_of_scans`, `exposure_time`, `scan_mode`, `reduction_mode`, pixel range), then push them to the device.
- **`capture_frame()` / `get_spectrum()`**: Trigger acquisition and read raw CCD buffer (ctypes `uint16` array, 3694 elements).
- **`subtract_background()`**: Returns 3653-element numpy array with edge-average background removed, sliced to pixel range `[32:3685]`.
- **`normalize_spectrum()`**: Calls `subtract_background()`, divides by `norm_coef`. Returns `(wavelength_array, intensity_array)`.
- **`get_calibrated_spectrum()`**: Calls `normalize_spectrum()`, multiplies by `power_coef / (exposure_time * bck_aT)`. Returns `(wavelength, uW/cm²/nm)`.
- **Calibration lazy-load**: On first call to any method needing calibration data, reads device flash memory once via `readFlash()` and caches `wavelength` (3653 values, flash lines 12–3665), `norm_coef` (lines 3666–7319), and `power_coef` (lines 7320–10973).
- **`__del__`**: Calls `disconnectDevice()` for cleanup.

### Device Parameters

| Parameter | Default | Range |
|---|---|---|
| `num_of_scans` | 1 | 1–137 |
| `exposure_time` | 1000 | ≥1000 (units: 10 µs) |
| `scan_mode` | 3 | 0–3 |
| `reduction_mode` | 0 | 0–3 (pixel binning: none, 2:1, 4:1, 8:1) |
| `num_of_start_element` | 0 | 0–3647 |
| `num_of_end_element` | 3647 | 0–3647 |

### Scan Modes (`scan_mode` parameter)

| Mode | Name | Behavior |
|---|---|---|
| 0 | Continuous | CCD reads continuously; trigger stores `num_of_scans` frames |
| 1 | First Frame Idle | CCD idle until trigger; reads `num_of_scans` frames then idles again |
| 2 | Every Frame Idle | CCD idle; each frame (including blanks) requires its own trigger |
| 3 | Frame Averaging | CCD reads continuously; every `num_of_scans` frames are averaged. `num_of_blank_scans` must be 0. Retrieve with `getFrame(0xFFFF)` |

### C API — Full Function List

`libspec.py` only wraps a subset. The native library also exposes these unwrapped functions:

| Function | Purpose |
|---|---|
| `resetDevice()` | Factory reset; reverts all params to defaults |
| `detachDevice()` | Soft-disconnect; device re-enumerates from defaults |
| `setExposure(time, force)` | Change exposure mid-acquisition (applies next frame) |
| `getAcquisitionParameters(...)` | Read back current acquisition params from device |
| `setExternalTrigger(enableMode, signalFrontMode)` | Hardware trigger: edge (rising=1, falling=2, both=3); one-shot (enableMode=2) |
| `setOpticalTrigger(enableMode, pixel, threshold)` | Pixel intensity threshold trigger (scanMode=0 only) |
| `clearMemory()` | Erase captured frames from device RAM |
| `getFrameFormat(...)` | Read back current pixel range and reduction mode |
| `setAllParameters(...)` | Combined acquisition params + external trigger in one call |
| `eraseFlash()` | Erase entire 128 KB user flash (5 s operation, no partial erase) |
| `writeFlash(buf, offset, len)` | Write to flash; can only write to 0xFF (erased) locations |

Factory defaults after `resetDevice()`: `numOfScans=1`, `numOfBlankScans=0`, `scanMode=0`, `exposureTime=10` (100 µs), full pixel range, `reductionMode=0`.

### Error Codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 500 | Wrong USB device ID |
| 501 | Device not found |
| 502 | USB connection failed |
| 503 | Device not initialized (not connected) |
| 504 | USB write failed |
| 505 | USB read timeout/failure |
| 506 | Unexpected response code from device |
| 507 | Frame packet count mismatch |
| 508 | Frame exceeds 124-packet maximum |
| 509 | NULL pointer passed for required output |
| 510 | Flash read packet count mismatch |

### `getStatus()` Flags

- `statusFlags & 0x01` — acquisition active
- `statusFlags & 0x02` — device memory full (must call `clearMemory()` before restarting)
- `framesInMemory == 2` in averaging mode — average ready but at least one frame was dropped (buffer overrun)

### Hardware Identity

- USB VID: `0xE220`, PID: `0x0100`
- Communication: USB HID (HIDAPI), 64-byte packets
- CCD: 3653 active pixels; default frame range 0–3647 (3648 pixels)
- Wavelength range: ~242.9 nm – ~951.6 nm

### Calibration Data Format (Flash Memory)

The flash stores a text file (ASCII, one value per line):

| Lines (1-indexed) | Content |
|---|---|
| 1 | Model / calibration flag (`cY`/`cN`) / serial number |
| 2 | Irradiation coefficient (float) |
| 3–12 | Empty padding |
| 13–3665 | Wavelength per CCD pixel (3653 floats, nm) |
| 3666 | Empty |
| 3667–7319 | Normalization coefficients (3653 floats) |
| 7320 | Empty |
| 7321–9973 | XYZ/power coefficients (3653 floats, used for uW/cm²/nm) |

> Note: `libspec.py` references these as 0-indexed Python list indices (e.g. index 12 = line 13). Cross-check if calibration values look wrong.

### Flash Memory Constraints

- Total user space: 128 KB (addresses `0x00000`–`0x1FFFF`)
- Write is destructive-only: can only write to 0xFF (erased) cells
- No partial erase — `eraseFlash()` clears all 128 KB; takes up to 5 seconds
- Max 58 bytes per USB write packet; max 60 bytes per read packet

## Known Issues

- `spectrum_norm.py` line 33 calls `get_calibrated_spectrum()` instead of `normalize_spectrum()` — likely a bug.
- README line 125 imports from `"libspectr"` instead of `"libspec"` (typo in docs, not code).
- `libspec.py` default `exposure_time=1000` (10 ms) differs from the device factory default of `10` (100 µs); the device resets to 10 after `resetDevice()`.

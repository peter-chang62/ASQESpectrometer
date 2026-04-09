# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ASQESpectrometer is a Python control API for the ASQE spectrometer hardware by PVSensors. It wraps a platform-specific C library (`libspectr`) via ctypes to provide device connection, acquisition, and calibrated spectral data processing.

## Setup

```bash
pip install -r requirements.txt
```

No build step required. Platform-specific compiled libraries are pre-built in `lib/` (`.dylib` for macOS, `.so` for Linux, `.dll` / `64bit.dll` for Windows).

## Running the Example Scripts

```bash
python spectrum.py        # Real-time raw normalized spectrum (press 'q' to exit)
python spectrum_norm.py   # Real-time calibrated spectrum (physical units: uW/cm²/nm)
python spectrum_calib.py  # Alternative calibrated spectrum visualization
```

## Architecture

The project is a thin ctypes wrapper with a processing pipeline layered on top:

```
lib/libspectr.[dylib|so|dll]  (C hardware library)
        ↓ ctypes bindings
   libspec.py (ASQESpectrometer class)
        ↓
   spectrum*.py  (example scripts using matplotlib real-time plots)
```

### `libspec.py` — Core Class

`ASQESpectrometer` wraps the C library and exposes a staged processing pipeline:

| Method | Output |
|--------|--------|
| `capture_frame()` | Raw 3694-element uint16 array from hardware |
| `subtract_background()` | 3653-element array with edge-pixel background removed |
| `normalize_spectrum()` | `(wavelengths, intensity)` using normalization coefficients from flash |
| `get_calibrated_spectrum()` | `(wavelengths, power)` in uW/cm²/nm, applying power coefficients + temperature correction |

### Calibration Data

Stored in device flash memory as a UTF-8 text file. Lazy-loaded on first call to `normalize_spectrum()` or `get_calibrated_spectrum()`, then cached on the instance. Layout:

- Line 1: `bck_aT` (background temperature coefficient)
- Lines 12–3664: wavelength array (3653 values)
- Lines 3666–7318: normalization coefficients (3653 values)
- Lines 7320–10972: power calibration coefficients (3653 values)

### Default Acquisition Parameters

```python
num_of_scans = 1
num_of_blank_scans = 0
exposure_time = 1000  # multiples of 10 µs → 10 ms
scan_mode = 3         # frame averaging
num_of_start_element = 0
num_of_end_element = 3647
reduction_mode = 0    # no pixel averaging
```

Note: `exposure_time` is in **multiples of 10 µs** per the C API (see `func_descr.txt`). Value `1000` = 10,000 µs = 10 ms.

## Reference Materials (`All software to download/`)

This folder contains vendor-supplied resources:

- **`DLL_source_code/libspectr.c` / `libspectr.h`** — C source for the compiled libraries in `lib/`. The authoritative reference for what the C API actually does.
- **`func_descr.txt`** — Complete C API documentation for all library functions. Consult this when adding new bindings or debugging ctypes signatures.
- **`Calibration information structure.pdf`** — Documents the flash memory calibration data format.
- **`protocol.pdf`** — Low-level USB protocol documentation.
- Language-specific examples: `Example_VBasic_net/`, `Example_VC6++/`, `LabView_Examples/`, `MathLab/` — reference implementations for porting or cross-checking behavior.
- **`Multi-spectrometers-communication/`** — Examples for connecting multiple devices simultaneously (uses `connectToDeviceByIndex()` / `getDeviceCount()`).

### C API Functions Not Yet Exposed in `libspec.py`

The C library has several functions not currently wrapped:

| Function | Purpose |
|----------|---------|
| `clearMemory()` | Clear frame buffer without resetting parameters |
| `eraseFlash()` | Erase all 128 KB of user flash (irreversible without full erase) |
| `writeFlash()` | Write calibration or user data to flash |
| `resetDevice()` | Reset device to factory defaults |
| `detachDevice()` | Disconnect from USB until hardware reset |
| `setExternalTrigger()` | Configure hardware trigger input |
| `setOpticalTrigger()` | Trigger acquisition on optical threshold (scan mode 0 only) |
| `setExposure()` | Change exposure mid-acquisition (applies on next frame) |
| `connectToDeviceByIndex()` | Connect to a specific device by index (multi-device) |
| `getDeviceCount()` | Count connected spectrometers |

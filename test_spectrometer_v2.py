"""
Integration tests for ASQESpectrometer — version 2.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v2.py

What's new vs v1:
  - Phase 0: raw HID diagnostic — prints the literal bytes the device sends
    for a bare getStatus write so we can see exactly where 0x0D comes from.
  - Phase 1: device reset before any other command, then verify with get_status().
  - Pre-flight: "not found" hint is shown ONLY when the device is missing.
  - More diagnostic output at each failure point.

Requirements:
  - ASQE Spectrometer connected via USB (VID=0x20E2, PID=0x0001)
  - ALL other HID clients closed (LabView, etc.)
  - conda env38 with hid and numpy installed
"""

import sys
import struct
import traceback
from time import sleep
import numpy as np

# ── Simple test reporter ───────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    # Only show detail hint on failure (fixes v1 bug: hint always printed)
    suffix = f"  ({detail})" if (detail and not condition) else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


# ── Pre-flight: verify hid is installed and device is visible ─────────────────

print("\n=== Pre-flight Checks ===")
try:
    import hid as _hid
    check("hid module is importable", True)
except ImportError as e:
    check("hid module is importable", False, str(e))
    print("\n  Install with: pip install hid")
    sys.exit(1)

devices = _hid.enumerate(0x20E2, 0x0001)
device_found = len(devices) > 0
if not check(
    "ASQE Spectrometer visible on USB (VID=0x20E2, PID=0x0001)",
    device_found,
    "not found — check USB cable and close LabView",
):
    print("\n  Device not found. Cannot continue.\n")
    sys.exit(1)

print(f"         path: {devices[0].get('path', b'?').decode(errors='replace')}")
print(f"         usage_page: 0x{devices[0].get('usage_page', 0):04X}")

# ── Phase 0: Raw HID diagnostic ───────────────────────────────────────────────
#
# Open the device with a bare hid.device() (not through ASQESpectrometer),
# send a single getStatus packet, and print the raw bytes returned.
# This tells us:
#   len=64, raw[0]=0x81 → Windows (report ID stripped), correct reply
#   len=65, raw[0]=0x00, raw[1]=0x81 → Linux-style (report ID included), correct
#   len=64, raw[0]=0x0D → device is in a bad state, sends 0x0D
#   len=65, raw[0]=0x00, raw[1]=0x0D → same bad-state response via Linux path
#
print("\n=== Phase 0: Raw HID Diagnostic ===")
_raw_dev = _hid.device()
try:
    _raw_dev.open(0x20E2, 0x0001)
    _raw_dev.set_nonblocking(False)

    # Build and send a bare getStatus (0x01) packet — 65 bytes, no helpers
    pkt = [0x00, 0x01] + [0x00] * 63   # report ID + opcode + zeros
    written = _raw_dev.write(pkt)
    print(f"  hid.write() returned {written} (expected 65)")

    sleep(0.05)

    raw = _raw_dev.read(65, 200)
    print(f"  hid.read(65, 200) returned {len(raw)} bytes")
    print(f"  raw[:8] = {[hex(b) for b in raw[:8]]}")

    if len(raw) == 65 and raw[0] == 0x00:
        opcode = raw[1]
        print(f"  → 65-byte read, report ID present; effective opcode = 0x{opcode:02X}")
    elif len(raw) == 64:
        opcode = raw[0]
        print(f"  → 64-byte read, report ID stripped; effective opcode = 0x{opcode:02X}")
    else:
        opcode = raw[0] if raw else None
        print(f"  → Unexpected read length {len(raw)}")

    if opcode == 0x81:
        check("Raw getStatus reply opcode is 0x81 (device idle, protocol OK)", True)
    elif opcode == 0x0D:
        check(
            "Raw getStatus reply opcode is 0x81 (device idle, protocol OK)",
            False,
            "got 0x0D — device left in bad state; Phase 1 reset should fix this",
        )
    else:
        check(
            "Raw getStatus reply opcode is 0x81 (device idle, protocol OK)",
            False,
            f"got 0x{opcode:02X if opcode is not None else 'XX'}",
        )
except Exception as e:
    check("Raw HID diagnostic completed without exception", False, str(e))
    traceback.print_exc()
finally:
    try:
        _raw_dev.close()
    except Exception:
        pass

# ── Phase 1: Connection + device reset ────────────────────────────────────────

print("\n=== Phase 1: Connection + Device Reset ===")
spec = None
try:
    from libspec import ASQESpectrometer
    spec = ASQESpectrometer()
    check("ASQESpectrometer() creates without exception", True)
    check("_dev is not None", spec._dev is not None)
    check("_num_pixels_in_frame is 0 before configure_acquisition",
          spec._num_pixels_in_frame == 0)
except Exception as e:
    check("ASQESpectrometer() creates without exception", False, str(e))
    traceback.print_exc()
    print("\n  Connection failed. Cannot continue.\n")
    sys.exit(1)

# Reset device to factory state — clears any leftover LabView acquisition mode.
print("  Sending resetDevice (0xF1)...")
try:
    spec.reset_device()
    check("reset_device() raises no exception", True)
except Exception as e:
    check("reset_device() raises no exception", False, str(e))
    traceback.print_exc()

print("  Waiting 1.5 s for device to re-initialize...")
sleep(1.5)

# Verify device is alive and idle after reset.
try:
    acq_active, mem_full, frames = spec.get_status()
    check("get_status() after reset raises no exception", True)
    check(
        "acquisition not active after reset",
        not acq_active,
        f"acq_active={acq_active}",
    )
    check(
        "device memory not full after reset",
        not mem_full,
        f"mem_full={mem_full}  — call clear_memory() to fix",
    )
    print(f"         framesInMemory={frames}, acq_active={acq_active}, mem_full={mem_full}")
    if mem_full:
        print("  Clearing device memory...")
        try:
            spec.clear_memory()
            print("  clear_memory() OK")
        except Exception as e2:
            print(f"  clear_memory() failed: {e2}")
except Exception as e:
    check("get_status() after reset raises no exception", False, str(e))
    traceback.print_exc()

# ── Phase 2: configure_acquisition ────────────────────────────────────────────

print("\n=== Phase 2: configure_acquisition ===")
try:
    spec.configure_acquisition()
    check("configure_acquisition() raises no exception", True)
    check(
        "_num_pixels_in_frame == 3648 (default full range, no binning)",
        spec._num_pixels_in_frame == 3648,
        f"got {spec._num_pixels_in_frame}",
    )
except Exception as e:
    check("configure_acquisition()", False, str(e))
    traceback.print_exc()

# 2:1 binning test
try:
    spec.set_parameters(reduction_mode=1)
    spec.configure_acquisition()
    check(
        "reduction_mode=1 → _num_pixels_in_frame == 1824",
        spec._num_pixels_in_frame == 1824,
        f"got {spec._num_pixels_in_frame}",
    )
    spec.set_parameters(reduction_mode=0)
    spec.configure_acquisition()
    check(
        "reset to reduction_mode=0 restores 3648 pixels",
        spec._num_pixels_in_frame == 3648,
        f"got {spec._num_pixels_in_frame}",
    )
except Exception as e:
    check("reduction_mode=1 binning test", False, str(e))
    traceback.print_exc()
    try:
        spec.set_parameters(reduction_mode=0)
        spec.configure_acquisition()
    except Exception:
        pass

# ── Phase 3: capture_frame ────────────────────────────────────────────────────

print("\n=== Phase 3: capture_frame ===")
buf = None
try:
    buf = spec.capture_frame()
    check("capture_frame() raises no exception", True)
    check("len(buf) == 3694", len(buf) == 3694, f"got {len(buf)}")
    check("buf.dtype == np.uint16", buf.dtype == np.uint16, f"got {buf.dtype}")
    check(
        "buf contains non-zero values (device is returning data)",
        np.any(buf > 0),
        "all zeros — check that shutter is not closed and exposure_time is adequate",
    )
    print(f"         buf[32:36] = {list(buf[32:36])}  (first active pixels)")
    print(f"         buf max    = {int(buf.max())}  min = {int(buf.min())}")
except Exception as e:
    check("capture_frame()", False, str(e))
    traceback.print_exc()

# ── Phase 4: subtract_background / normalize_spectrum ─────────────────────────

print("\n=== Phase 4: subtract_background / normalize_spectrum ===")
try:
    corrected = spec.subtract_background()
    check("subtract_background() raises no exception", True)
    check(
        "subtract_background() returns 3653 elements",
        len(corrected) == 3653,
        f"got {len(corrected)}",
    )
    check(
        "corrected array dtype is float",
        np.issubdtype(corrected.dtype, np.floating),
        f"got {corrected.dtype}",
    )
    print(f"         corrected[1626] = {corrected[1626]:.1f}  (mid-range pixel)")
except Exception as e:
    check("subtract_background()", False, str(e))
    traceback.print_exc()

try:
    wl, intensity = spec.normalize_spectrum()
    check("normalize_spectrum() raises no exception", True)
    check("wavelength has 3653 elements", len(wl) == 3653, f"got {len(wl)}")
    check("intensity has 3653 elements", len(intensity) == 3653, f"got {len(intensity)}")
except Exception as e:
    check("normalize_spectrum()", False, str(e))
    traceback.print_exc()

# ── Phase 5: get_calibrated_spectrum ──────────────────────────────────────────

print("\n=== Phase 5: get_calibrated_spectrum ===")
try:
    wl, power = spec.get_calibrated_spectrum()
    check("get_calibrated_spectrum() raises no exception", True)
    wl_min, wl_max = float(wl[0]), float(wl[-1])
    check(
        "wavelength range is ~242–952 nm",
        200 < wl_min < 300 and 900 < wl_max < 1000,
        f"got {wl_min:.1f}–{wl_max:.1f} nm",
    )
    check(
        "power array has no NaN/Inf",
        np.all(np.isfinite(power)),
        f"{np.sum(~np.isfinite(power))} bad values",
    )
    print(f"         wavelength[0]={wl_min:.2f} nm  wavelength[-1]={wl_max:.2f} nm")
except Exception as e:
    check("get_calibrated_spectrum()", False, str(e))
    traceback.print_exc()

# ── Phase 6: Flash read ────────────────────────────────────────────────────────

print("\n=== Phase 6: Flash read ===")
try:
    chunk = spec.read_flash(offset=0, size=100)
    check("read_flash(size=100) returns 100 bytes", len(chunk) == 100, f"got {len(chunk)}")
    print(f"         first 8 bytes: {list(chunk[:8])}")
except Exception as e:
    check("read_flash(size=100)", False, str(e))
    traceback.print_exc()

try:
    print("  (reading full calibration file — may take ~30 s) ...")
    calib_raw = spec.read_calibration_file()
    check(
        "read_calibration_file() returns non-empty data",
        len(calib_raw) > 0,
        f"{len(calib_raw)} bytes",
    )
    try:
        decoded = calib_raw.decode("utf-8")
        lines = decoded.splitlines()
        check("calibration file is valid UTF-8", True)
        check(
            "calibration file has >= 10973 lines",
            len(lines) >= 10973,
            f"got {len(lines)} lines",
        )
        # Print first few lines for sanity-check
        print(f"         line 0: {lines[0][:60]!r}")
        print(f"         line 1 (bck_aT): {lines[1][:40]!r}")
        try:
            val = float(lines[1])
            check(f"bck_aT (line 2) parses as float: {val:.6g}", True)
        except (ValueError, IndexError) as e2:
            check("bck_aT (line 2) parses as float", False, str(e2))
        # Spot-check wavelength at index 12
        try:
            wl_first = float(lines[12])
            check(
                f"wavelength[0] (index 12) parses as float: {wl_first:.3f} nm",
                200 < wl_first < 300,
                f"expected ~242 nm, got {wl_first}",
            )
        except (ValueError, IndexError) as e3:
            check("wavelength[0] (index 12) parses as float", False, str(e3))
    except UnicodeDecodeError as e:
        check("calibration file is valid UTF-8", False, str(e))
except Exception as e:
    check("read_calibration_file()", False, str(e))
    traceback.print_exc()

# ── Summary ────────────────────────────────────────────────────────────────────

print("\n" + "=" * 55)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")
if failed == 0:
    print("  All tests passed.")
    print("  Next steps:")
    print("    python spectrum.py         # raw normalized spectrum")
    print("    python spectrum_norm.py    # power-calibrated (uW/cm²/nm)")
else:
    failed_names = [name for name, ok in _results if not ok]
    print("  Failed tests:")
    for name in failed_names:
        print(f"    - {name}")
print("=" * 55 + "\n")

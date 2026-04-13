"""
Integration tests for the ASQESpectrometer pure-Python HID reimplementation.

Run directly:  python test_spectrometer.py
Run via pytest: pytest test_spectrometer.py -v

Requirements:
  - ASQE Spectrometer connected via USB
  - ALL other HID clients closed (LabView, etc.) — only one process can hold the HID device
  - hid module installed: pip install hid

These are ordered integration tests; later phases depend on earlier ones passing.
If Phase 1 (connection) fails, the script exits immediately.
"""

import sys
import traceback
import numpy as np

# ── Simple test reporter ───────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
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
if not check(
    "ASQE Spectrometer visible on USB (VID=0x20E2, PID=0x0001)",
    len(devices) > 0,
    "not found — check USB cable and close LabView",
):
    print("\n  Device not found. Cannot continue.\n")
    sys.exit(1)

# ── Phase 1: Connection ────────────────────────────────────────────────────────

print("\n=== Phase 1: Connection ===")
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

# Test 2:1 pixel binning
try:
    spec.set_parameters(reduction_mode=1)
    spec.configure_acquisition()
    check(
        "reduction_mode=1 → _num_pixels_in_frame == 1824",
        spec._num_pixels_in_frame == 1824,
        f"got {spec._num_pixels_in_frame}",
    )
    # Restore defaults
    spec.set_parameters(reduction_mode=0)
    spec.configure_acquisition()
    check("reset to reduction_mode=0 restores 3648 pixels",
          spec._num_pixels_in_frame == 3648,
          f"got {spec._num_pixels_in_frame}")
except Exception as e:
    check("reduction_mode=1 binning test", False, str(e))
    traceback.print_exc()
    # Attempt to recover
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
    print(f"         buf[32:36] = {list(buf[32:36])}  (first active pixels, for reference)")
except Exception as e:
    check("capture_frame()", False, str(e))
    traceback.print_exc()

# ── Phase 4: subtract_background / normalize_spectrum ─────────────────────────

print("\n=== Phase 4: subtract_background / normalize_spectrum ===")
try:
    corrected = spec.subtract_background()
    check("subtract_background() raises no IndexError", True)
    check(
        "subtract_background() returns 3653 elements",
        len(corrected) == 3653,
        f"got {len(corrected)}",
    )
    check("corrected array dtype is float", np.issubdtype(corrected.dtype, np.floating),
          f"got {corrected.dtype}")
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
    check("power array has no NaN/Inf", np.all(np.isfinite(power)),
          f"{np.sum(~np.isfinite(power))} bad values")
except Exception as e:
    check("get_calibrated_spectrum()", False, str(e))
    traceback.print_exc()

# ── Phase 6: Flash read ────────────────────────────────────────────────────────

print("\n=== Phase 6: Flash read ===")
try:
    chunk = spec.read_flash(offset=0, size=100)
    check("read_flash(size=100) returns 100 bytes", len(chunk) == 100, f"got {len(chunk)}")
except Exception as e:
    check("read_flash(size=100)", False, str(e))
    traceback.print_exc()

try:
    print("  (reading full calibration file — may take ~30 s) ...")
    calib_raw = spec.read_calibration_file()
    check("read_calibration_file() returns non-empty data",
          len(calib_raw) > 0, f"{len(calib_raw)} bytes")
    try:
        decoded = calib_raw.decode("utf-8")
        lines = decoded.splitlines()
        check("calibration file is valid UTF-8", True)
        check(
            "calibration file has >= 10973 lines",
            len(lines) >= 10973,
            f"got {len(lines)} lines",
        )
        # Spot-check: line 2 (index 1) should be a float (bck_aT)
        try:
            val = float(lines[1])
            check(f"bck_aT (line 2) parses as float: {val}", True)
        except (ValueError, IndexError) as e2:
            check("bck_aT (line 2) parses as float", False, str(e2))
    except UnicodeDecodeError as e:
        check("calibration file is valid UTF-8", False, str(e))
except Exception as e:
    check("read_calibration_file()", False, str(e))
    traceback.print_exc()

# ── Summary ────────────────────────────────────────────────────────────────────

print("\n" + "=" * 55)
total = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")
if failed == 0:
    print("  All tests passed.")
    print("  Next steps:")
    print("    python spectrum.py         # raw normalized spectrum")
    print("    python spectrum_norm.py    # power-calibrated (uW/cm²/nm)")
    print("    python spectrum_calib.py   # also normalized (see Known Issues)")
else:
    failed_names = [name for name, ok in _results if not ok]
    print("  Failed tests:")
    for name in failed_names:
        print(f"    - {name}")
    print("  See LAB_PC_INSTRUCTIONS.md for troubleshooting guidance.")
print("=" * 55 + "\n")


# ── pytest compatibility ───────────────────────────────────────────────────────
# When running via pytest, expose individual test functions.
# These re-use the module-level `spec` object created above.

def test_connection():
    assert spec is not None
    assert spec._dev is not None


def test_configure_acquisition():
    spec.set_parameters(reduction_mode=0)
    spec.configure_acquisition()
    assert spec._num_pixels_in_frame == 3648


def test_capture_frame():
    buf = spec.capture_frame()
    assert len(buf) == 3694
    assert buf.dtype == np.uint16


def test_subtract_background():
    corrected = spec.subtract_background()
    assert len(corrected) == 3653


def test_normalize_spectrum():
    wl, intensity = spec.normalize_spectrum()
    assert len(wl) == 3653
    assert len(intensity) == 3653


def test_calibrated_spectrum():
    wl, power = spec.get_calibrated_spectrum()
    wl_min, wl_max = float(wl[0]), float(wl[-1])
    assert 200 < wl_min < 300
    assert 900 < wl_max < 1000


def test_flash_read():
    chunk = spec.read_flash(offset=0, size=100)
    assert len(chunk) == 100

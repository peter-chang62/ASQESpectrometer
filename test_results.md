# Test Results — test_spectrometer.py

**Date:** 2026-04-13  
**Environment:** conda `env38` (Python 3.8.19)  
**Command:** `conda run --no-capture-output -n env38 python test_spectrometer.py`

---

## Summary

| | |
|---|---|
| **Total tests** | 13 |
| **Passed** | 5 |
| **Failed** | 8 |
| **Overall result** | FAIL |

---

## Results by Phase

### Pre-flight Checks
| Test | Result | Notes |
|---|---|---|
| `hid` module is importable | PASS | |
| ASQE Spectrometer visible on USB (VID=0x20E2, PID=0x0001) | PASS | Not found — check USB cable and close LabView |

> **Note:** The USB pre-flight check reports PASS but appends "not found." Device connectivity was confirmed independently via LabView; LabView was fully closed before this test run. The "not found" message reflects a bug or limitation in the pre-flight detection logic, not a missing device.

### Phase 1: Connection
| Test | Result | Notes |
|---|---|---|
| `ASQESpectrometer()` creates without exception | PASS | |
| `_dev` is not None | PASS | |
| `_num_pixels_in_frame` is 0 before `configure_acquisition` | PASS | |

### Phase 2: configure_acquisition
| Test | Result | Notes |
|---|---|---|
| `configure_acquisition()` | FAIL | Wrong reply opcode: expected 0x83, got 0x0D (error 506) |
| `reduction_mode=1` binning test | FAIL | Wrong reply opcode: expected 0x83, got 0x0D (error 506) |

### Phase 3: capture_frame
| Test | Result | Notes |
|---|---|---|
| `capture_frame()` | FAIL | Wrong reply opcode: expected 0x81, got 0x0D (error 506) |

### Phase 4: subtract_background / normalize_spectrum
| Test | Result | Notes |
|---|---|---|
| `subtract_background()` | FAIL | Wrong reply opcode: expected 0x81, got 0x0D (error 506) |
| `normalize_spectrum()` | FAIL | HID read timeout after 100 ms (error 505) |

### Phase 5: get_calibrated_spectrum
| Test | Result | Notes |
|---|---|---|
| `get_calibrated_spectrum()` | FAIL | HID read timeout after 100 ms (error 505) |

### Phase 6: Flash read
| Test | Result | Notes |
|---|---|---|
| `read_flash(size=100)` | FAIL | HID read timeout after 100 ms (error 505) |
| `read_calibration_file()` | FAIL | HID read timeout after 100 ms (error 505) |

---

## Error Analysis

Two distinct error codes appeared:

**Error 506 — Unexpected response code from device**  
Phases 2–4 received opcode `0x0D` when a valid response opcode was expected (`0x83`, `0x81`). Opcode `0x0D` (`\r`, carriage return) is not a valid device response. Device connectivity was independently verified via LabView and no other process was holding the HID handle at test time, so the garbage opcode indicates a protocol-level issue in the pure-Python HID implementation rather than a device access conflict.

**Error 505 — USB read timeout**  
Phases 4–6 timed out after 100 ms with no response. The flash read commands were never acknowledged, likely because the device was already in a bad communication state from the 506 errors above.

**Root cause hypothesis:** The device is physically connected and accessible. The `0x0D` opcode received in error 506 suggests the Python HID driver is either sending a malformed command packet or misreading the response framing. The issue lies within `libspec.py`'s `_write_read` / packet construction logic, not in device availability.

---

## Raw Output

```
=== Pre-flight Checks ===
  [PASS] hid module is importable
  [PASS] ASQE Spectrometer visible on USB (VID=0x20E2, PID=0x0001)  (not found — check USB cable and close LabView)

=== Phase 1: Connection ===
  [PASS] ASQESpectrometer() creates without exception
  [PASS] _dev is not None
  [PASS] _num_pixels_in_frame is 0 before configure_acquisition

=== Phase 2: configure_acquisition ===
  [FAIL] configure_acquisition()  (Wrong reply opcode: expected 0x83, got 0x0D (error 506))
  [FAIL] reduction_mode=1 binning test  (Wrong reply opcode: expected 0x83, got 0x0D (error 506))

=== Phase 3: capture_frame ===
  [FAIL] capture_frame()  (Wrong reply opcode: expected 0x81, got 0x0D (error 506))

=== Phase 4: subtract_background / normalize_spectrum ===
  [FAIL] subtract_background()  (Wrong reply opcode: expected 0x81, got 0x0D (error 506))
  [FAIL] normalize_spectrum()  (HID read timeout after 100 ms (error 505))

=== Phase 5: get_calibrated_spectrum ===
  [FAIL] get_calibrated_spectrum()  (HID read timeout after 100 ms (error 505))

=== Phase 6: Flash read ===
  [FAIL] read_flash(size=100)  (HID read timeout after 100 ms (error 505))
  (reading full calibration file — may take ~30 s) ...
  [FAIL] read_calibration_file()  (HID read timeout after 100 ms (error 505))

=======================================================
  Results: 5/13 passed,  8 failed
  Failed tests:
    - configure_acquisition()
    - reduction_mode=1 binning test
    - capture_frame()
    - subtract_background()
    - normalize_spectrum()
    - get_calibrated_spectrum()
    - read_flash(size=100)
    - read_calibration_file()
  See LAB_PC_INSTRUCTIONS.md for troubleshooting guidance.
=======================================================
```

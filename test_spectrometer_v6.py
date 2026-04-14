"""
Diagnostic test for ASQE Spectrometer — version 6.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v6.py

v5 outcome
----------
  - Hypothesis C confirmed: Windows HIDAPI prepends report-ID byte 0x0D to every
    read; device echoes the command opcode in byte[1] (no high-bit set).
  - Response format: [0x0D, cmd_echo, payload...] = 64 bytes total.
  - v5 conclusion logic was buggy (setFrameFormat timed out, invalidated all-commands
    check), but the raw data for getStatus and getFrameFormat was unambiguous.

Fixes applied to libspec.py
----------------------------
  1. _normalize_response(): strips 0x0D prefix on Windows → normalized buffer is
     [cmd_echo, payload...] (63 bytes after stripping).
  2. All expected_reply values changed from cmd|0x80 to cmd throughout.

This test
---------
  Uses ASQESpectrometer directly to verify end-to-end communication.

  Phase 1 — connect + get_status()
      Instantiate ASQESpectrometer; call get_status().
      PASS: no exception, status tuple printed.

  Phase 2 — configure_acquisition()
      Call configure_acquisition() with default parameters.
      PASS: no exception; _num_pixels_in_frame printed (expect 3648).

  Phase 3 — capture_frame()
      Trigger one frame with 10 ms exposure (exposure_time=1000).
      PASS: numpy array returned, shape (3694,), non-trivial values printed.
"""

import sys
import traceback

# ── Reporter ───────────────────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


# ── Phase 1: connect + get_status ─────────────────────────────────────────────

print("\n=== Phase 1: connect + get_status ===")

spec = None
try:
    from libspec import ASQESpectrometer
    spec = ASQESpectrometer()
    acq_active, mem_full, frames_in_memory = spec.get_status()
    print(f"  acq_active={acq_active}  mem_full={mem_full}  frames_in_memory={frames_in_memory}")
    check("connect + get_status", True)
except Exception as e:
    check("connect + get_status", False, str(e))
    traceback.print_exc()

# ── Phase 2: configure_acquisition ────────────────────────────────────────────

print("\n=== Phase 2: configure_acquisition ===")

if spec is None:
    print("  skipped — Phase 1 failed (no device handle)")
    _results.append(("configure_acquisition", False))
else:
    try:
        spec.configure_acquisition()
        px = spec._num_pixels_in_frame
        print(f"  _num_pixels_in_frame = {px}  (expect 3648)")
        check("configure_acquisition", True,
              f"pixel count {px}" + ("" if px == 3648 else " — unexpected value"))
    except Exception as e:
        check("configure_acquisition", False, str(e))
        traceback.print_exc()

# ── Phase 3: capture_frame ────────────────────────────────────────────────────

print("\n=== Phase 3: capture_frame ===")

if spec is None:
    print("  skipped — Phase 1 failed")
    _results.append(("capture_frame", False))
else:
    try:
        import numpy as np
        frame = spec.capture_frame()
        shape_ok = hasattr(frame, 'shape') and frame.shape == (3694,)
        print(f"  shape={frame.shape}  dtype={frame.dtype}")
        print(f"  min={frame.min()}  max={frame.max()}  mean={frame.mean():.1f}")
        check("capture_frame returns (3694,) uint16 array", shape_ok,
              f"got shape {frame.shape}" if not shape_ok else "")
        check("capture_frame has non-zero data", int(frame.max()) > 0)
    except Exception as e:
        check("capture_frame", False, str(e))
        traceback.print_exc()

# ── Cleanup ────────────────────────────────────────────────────────────────────

if spec is not None:
    del spec

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")
print("=" * 60 + "\n")

"""
Diagnostic test for ASQE Spectrometer — version 3.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v3.py

Background / what v2 told us
-----------------------------
v2 showed:
    hid.write() returned 9 (expected 65)

hid.write() sends HID *output reports*.  The device's HID descriptor caps the
output report at 8 bytes; Windows enforces this.  Every 65-byte command packet
is silently truncated to 9 bytes (1 report-ID + 8 payload).  The device
receives a malformed packet and responds with opcode 0x0D (error) instead of
the expected 0x81/0x83/... reply.

Primary hypothesis
------------------
The DLL (libspectr.dll) likely uses HidD_SetFeature() — Windows API for HID
*feature reports* — which allows full 64-byte packets even when output reports
are limited.  Python hid exposes send_feature_report().

Fallback hypothesis
-------------------
USB composite devices can expose multiple HID interfaces.  hid.open(vid,pid)
picks the first match; there may be a second interface with 64-byte output
reports that the DLL targets.

This test probes both hypotheses, then runs the full command sequence with
whichever write method works.
"""

import sys
import struct
import traceback
from time import sleep

# ── Simple reporter ──────────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if (detail and not condition) else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


# ── Helpers ──────────────────────────────────────────────────────────────────

def flush_reads(dev, timeout_ms=30, max_reads=20):
    """Drain any stale packets from the HID receive buffer."""
    count = 0
    while count < max_reads:
        r = dev.read(65, timeout_ms)
        if not r:
            break
        count += 1
    if count:
        print(f"  (flushed {count} stale packet(s))")


def normalize(raw):
    """Normalize platform-specific read to 64-byte opcode-first buffer.
    Returns None on unrecognized length.
    """
    if not raw:
        return None
    if len(raw) == 65 and raw[0] == 0x00:
        return raw[1:]   # Linux: strip report ID
    if len(raw) == 64:
        return raw        # Windows/macOS: already stripped
    return None


# ── Pre-flight ───────────────────────────────────────────────────────────────

print("\n=== Pre-flight ===")
try:
    import hid as _hid
    check("hid module is importable", True)
except ImportError as e:
    check("hid module is importable", False, str(e))
    sys.exit(1)

VID, PID = 0x20E2, 0x0001

all_devs = _hid.enumerate(VID, PID)
if not check(
    f"Device visible on USB (VID=0x{VID:04X}, PID=0x{PID:04X})",
    len(all_devs) > 0,
    "not found — check USB cable and close LabView",
):
    sys.exit(1)

# ── Phase 0: Full HID enumeration ────────────────────────────────────────────

print(f"\n=== Phase 0: Full HID Enumeration ({len(all_devs)} interface(s)) ===")
print(f"  Multiple interfaces would mean hid.open(vid,pid) may pick the wrong one.")
for i, d in enumerate(all_devs):
    print(f"\n  Interface [{i}]:")
    for k, v in d.items():
        if isinstance(v, bytes):
            v = v.decode(errors="replace")
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        print(f"    {k}: {v}")

all_paths = [d.get("path", b"") for d in all_devs]

# ── Phase 1: Write size probe ─────────────────────────────────────────────────
#
# Determines the actual output report size enforced by the driver.
# If write(65 bytes) returns 9, the output report is limited to 8 bytes.

print("\n=== Phase 1: Write Size Probe ===")
print("  Sending different write sizes; each should return the number of bytes sent.")
_p1_dev = _hid.device()
try:
    _p1_dev.open(VID, PID)
    _p1_dev.set_nonblocking(False)
    flush_reads(_p1_dev)

    for size in [9, 65]:
        pkt = [0x00] * size
        pkt[1 % size] = 0x01  # opcode byte
        try:
            ret = _p1_dev.write(pkt)
            note = "OK" if ret == size else f"TRUNCATED — only {ret} of {size} bytes sent"
            print(f"    write({size:2d} bytes) → returned {ret}  [{note}]")
        except Exception as e:
            print(f"    write({size:2d} bytes) → exception: {e}")
        sleep(0.05)
        flush_reads(_p1_dev, timeout_ms=50)

    has_sfr = hasattr(_p1_dev, "send_feature_report")
    check(
        "hid.device has send_feature_report()",
        has_sfr,
        "update hid: pip install hid --upgrade",
    )
except Exception as e:
    print(f"  Phase 1 error: {e}")
    traceback.print_exc()
finally:
    try:
        _p1_dev.close()
    except Exception:
        pass

# ── Phase 2: Feature report probe ─────────────────────────────────────────────
#
# send_feature_report() maps to HidD_SetFeature() on Windows, which uses the
# *feature* report channel and is not capped by the output report size limit.

print("\n=== Phase 2: Feature Report Probe ===")
feature_report_works = False
_p2_dev = _hid.device()
try:
    _p2_dev.open(VID, PID)
    _p2_dev.set_nonblocking(False)
    flush_reads(_p2_dev)

    if not hasattr(_p2_dev, "send_feature_report"):
        print("  send_feature_report not available — skipping")
    else:
        pkt = [0x00, 0x01] + [0x00] * 63   # report-ID=0x00, opcode=0x01 (getStatus)
        try:
            ret = _p2_dev.send_feature_report(pkt)
            print(f"  send_feature_report(65 bytes) → returned {ret}")
            check("send_feature_report returned 65", ret == 65, f"got {ret}")

            sleep(0.15)
            raw = _p2_dev.read(65, 500)
            if not raw:
                print("  read after feature report: TIMEOUT — no response from device")
                check("feature report getStatus replies 0x81", False, "read timeout")
            else:
                print(f"  read after feature report: {len(raw)} bytes, "
                      f"first 8: {[hex(b) for b in raw[:8]]}")
                data = normalize(raw)
                if data is not None:
                    opcode = data[0]
                    print(f"  → effective opcode: 0x{opcode:02X}")
                    feature_report_works = check(
                        "feature report getStatus replies 0x81",
                        opcode == 0x81,
                        f"got 0x{opcode:02X}",
                    )
                else:
                    check("feature report getStatus replies 0x81", False,
                          f"unexpected read length {len(raw)}")
        except Exception as e:
            check("send_feature_report raises no exception", False, str(e))
            traceback.print_exc()
except Exception as e:
    print(f"  Phase 2 setup error: {e}")
    traceback.print_exc()
finally:
    try:
        _p2_dev.close()
    except Exception:
        pass

# ── Phase 3: Multi-interface path probe ──────────────────────────────────────
#
# Try opening each enumerated path explicitly and test write() + getStatus.
# The DLL might always open a specific interface that has 64-byte output reports.

print("\n=== Phase 3: Multi-Interface Path Probe ===")
working_path = None

for i, raw_path in enumerate(all_paths):
    path = raw_path if isinstance(raw_path, bytes) else raw_path.encode()
    path_str = path.decode(errors="replace")
    short = path_str[:60] + "..." if len(path_str) > 60 else path_str
    print(f"\n  Path [{i}]: {short}")

    _pi_dev = _hid.device()
    try:
        _pi_dev.open_path(path)
        _pi_dev.set_nonblocking(False)
        flush_reads(_pi_dev)

        pkt = [0x00, 0x01] + [0x00] * 63
        try:
            ret = _pi_dev.write(pkt)
            print(f"    write(65 bytes) → {ret}")
            sleep(0.15)
            raw = _pi_dev.read(65, 500)
            if not raw:
                print("    read: TIMEOUT")
                check(f"path[{i}] write() getStatus → 0x81", False, "read timeout")
            else:
                data = normalize(raw)
                opcode = data[0] if data else 0x00
                print(f"    read: {len(raw)} bytes, opcode 0x{opcode:02X}")
                passed = check(
                    f"path[{i}] write() getStatus → 0x81",
                    opcode == 0x81,
                    f"got 0x{opcode:02X}",
                )
                if passed and working_path is None:
                    working_path = path
                    print(f"    *** WORKING PATH FOUND ***")
        except Exception as e:
            check(f"path[{i}] write() raises no exception", False, str(e))
    except Exception as e:
        print(f"    open_path error: {e}")
        check(f"path[{i}] open_path raises no exception", False, str(e))
    finally:
        try:
            _pi_dev.close()
        except Exception:
            pass

# ── Determine working transport ───────────────────────────────────────────────

print("\n=== Transport Resolution ===")
if feature_report_works:
    write_method = "feature_report"
    print("  Working transport: send_feature_report()")
elif working_path is not None:
    write_method = "path"
    print(f"  Working transport: write() via path {working_path.decode(errors='replace')[:60]}")
else:
    write_method = None
    print("  No working write method found in Phases 2–3.")
    print("  → Try: unplug/replug USB, close all HID clients, re-run this test.")

# ── Phase 4: Full command sequence ────────────────────────────────────────────

print("\n=== Phase 4: Full Command Sequence ===")

if write_method is None:
    print("  Skipped — no working transport.")
else:
    try:
        from libspec import ASQESpectrometer
        import numpy as np

        # Monkey-patch the write transport to use whatever worked above
        if write_method == "feature_report":
            def _patched_write(self, opcode, payload=None):
                pkt = [0x00, opcode] + (payload or [])
                pkt += [0x00] * (65 - len(pkt))
                written = self._dev.send_feature_report(pkt)
                if written < 0:
                    raise RuntimeError("HID feature report write failed (error 504)")
            ASQESpectrometer._write = _patched_write
            print("  Applied patch: _write → send_feature_report()")

        elif write_method == "path":
            _wp = working_path
            def _patched_connect(self):
                self._dev.open_path(_wp)
                self._dev.set_nonblocking(False)
            ASQESpectrometer.connect = _patched_connect
            print("  Applied patch: connect → open_path(working_path)")

        # Construct device
        spec = None
        try:
            spec = ASQESpectrometer()
            check("ASQESpectrometer() creates without exception", True)
        except Exception as e:
            check("ASQESpectrometer() creates without exception", False, str(e))
            traceback.print_exc()

        if spec is not None:

            # Reset + wait
            print("  Sending resetDevice (0xF1)...")
            try:
                spec.reset_device()
                check("reset_device() raises no exception", True)
            except Exception as e:
                check("reset_device() raises no exception", False, str(e))
                traceback.print_exc()

            print("  Waiting 2 s for device to re-initialize...")
            sleep(2.0)

            # get_status
            try:
                acq_active, mem_full, frames = spec.get_status()
                check("get_status() raises no exception", True)
                check("acquisition not active after reset", not acq_active,
                      f"acq_active={acq_active}")
                check("device memory not full after reset", not mem_full,
                      f"mem_full={mem_full} — clear_memory() will be called")
                print(f"         framesInMemory={frames}, acq_active={acq_active}, mem_full={mem_full}")
                if mem_full:
                    try:
                        spec.clear_memory()
                        print("  clear_memory() OK")
                    except Exception as e2:
                        print(f"  clear_memory() failed: {e2}")
            except Exception as e:
                check("get_status() raises no exception", False, str(e))
                traceback.print_exc()

            # configure_acquisition
            try:
                spec.configure_acquisition()
                check("configure_acquisition() raises no exception", True)
                check(
                    "_num_pixels_in_frame == 3648 (full range, no binning)",
                    spec._num_pixels_in_frame == 3648,
                    f"got {spec._num_pixels_in_frame}",
                )
            except Exception as e:
                check("configure_acquisition()", False, str(e))
                traceback.print_exc()

            # capture_frame
            try:
                buf = spec.capture_frame()
                check("capture_frame() raises no exception", True)
                check("len(buf) == 3694", len(buf) == 3694, f"got {len(buf)}")
                check("buf.dtype == np.uint16", buf.dtype == np.uint16,
                      f"got {buf.dtype}")
                check("buf contains non-zero values", np.any(buf > 0),
                      "all zeros — check exposure_time or shutter")
                print(f"         buf[32:36] = {list(buf[32:36])}  (first active pixels)")
                print(f"         buf max={int(buf.max())}  min={int(buf.min())}")
            except Exception as e:
                check("capture_frame()", False, str(e))
                traceback.print_exc()

            # read_flash
            try:
                chunk = spec.read_flash(offset=0, size=100)
                check("read_flash(size=100) returns 100 bytes",
                      len(chunk) == 100, f"got {len(chunk)}")
                print(f"         first 8 bytes: {list(chunk[:8])}")
                snippet = chunk.decode("utf-8", errors="replace")
                print(f"         first 60 chars: {snippet[:60]!r}")
            except Exception as e:
                check("read_flash(size=100)", False, str(e))
                traceback.print_exc()

    except ImportError as e:
        print(f"  Could not import libspec: {e}")

# ── Summary ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 55)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")

print("\n  Transport diagnosis:")
print(f"    feature_report_works = {feature_report_works}")
wpath_str = working_path.decode(errors="replace")[:60] if working_path else None
print(f"    working_path         = {wpath_str}")
print(f"    write_method         = {write_method}")

if write_method == "feature_report":
    print("\n  NEXT STEP — update libspec.py _write() (line ~92):")
    print("    BEFORE: written = self._dev.write(pkt)")
    print("    AFTER:  written = self._dev.send_feature_report(pkt)")
elif write_method == "path":
    print("\n  NEXT STEP — update libspec.py connect() to use open_path(working_path)")
elif write_method is None:
    print("\n  No working write method found.")
    print("  → Unplug/replug USB, close ALL HID clients (LabView, etc.), re-run.")

if failed == 0:
    print("\n  All tests passed.")

print("=" * 55 + "\n")

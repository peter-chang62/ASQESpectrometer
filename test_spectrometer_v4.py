"""
Diagnostic test for ASQE Spectrometer — version 4.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v4.py

What v3 found / what changed
------------------------------
v3 confirmed:
  - hid.write(65 bytes) → 9 bytes sent; Windows HID output report is capped at
    8 data bytes (1 report-ID + 8 payload = 9 bytes total).
  - send_feature_report(report_ID=0x00) → -1 (no feature report at ID 0x00).
  - After Phase 2's failed feature-report probe, Phase 3 saw opcode 0x0D.

The key unanswered question from v3:
  In v3 Phase 1, after write(9 bytes), the device responded — 1 packet was flushed
  WITHOUT being read.  We never saw if it was 0x81 (success) or 0x0D (error).
  Phase 3 tested the "same" 9-byte packet but ran AFTER the failed feature-report
  probe, which may have dirtied device state.  The responses are not comparable.

This test:
  Phase 1 — sends a 9-byte getStatus AS THE VERY FIRST WRITE (nothing before it),
             then READS (not flushes) the response.
  Phase 2 — tests setAcquisitionParameters with exposureTime packed as uint16
             (libspec.py currently uses uint32 = 4 bytes, which bloats the payload
             to 10 bytes, exceeding the 8-byte output report).
             uint16 (2 bytes) fits: 1+2+2+1+2 = 8 data bytes exactly.
  Phase 3 — full acquisition pipeline if Phase 1+2 pass.
  Phase 4 — feature report ID scan 0x01–0x07 (fallback, only if Phase 1 fails).
"""

import sys
import struct
import math
import traceback
from time import sleep

# ── Reporter ──────────────────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if (detail and not condition) else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


# ── HID helpers (9-byte writes) ───────────────────────────────────────────────

def write9(dev, opcode, payload=None):
    """Send a command as exactly 9 bytes (1 report-ID + 8 data)."""
    pkt = [0x00, opcode] + (payload or [])
    pkt = pkt[:9]
    pkt += [0x00] * (9 - len(pkt))
    return dev.write(pkt)


def read64(dev, timeout_ms=300):
    """Read one HID input packet, normalized to a 64-element list.
    Returns None on timeout.
    """
    raw = dev.read(65, timeout_ms)
    if not raw:
        return None
    if len(raw) == 65 and raw[0] == 0x00:
        return list(raw[1:])   # Linux: strip report-ID byte
    if len(raw) == 64:
        return list(raw)        # Windows/macOS: already stripped
    return None


def flush(dev, timeout_ms=30, max_reads=20):
    count = 0
    while count < max_reads:
        if not dev.read(65, timeout_ms):
            break
        count += 1
    if count:
        print(f"  (flushed {count} stale packet(s))")


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
    "device not found — check USB cable",
):
    sys.exit(1)

# ── Phase 0: Enumeration ──────────────────────────────────────────────────────

print(f"\n=== Phase 0: HID Enumeration ({len(all_devs)} interface(s)) ===")
for i, d in enumerate(all_devs):
    print(f"\n  Interface [{i}]:")
    for k, v in d.items():
        if isinstance(v, bytes):
            v = v.decode(errors="replace")
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        print(f"    {k}: {v}")

# ── Phase 1: 9-byte getStatus in isolation ────────────────────────────────────
#
# Open a fresh device, flush stale data, then send a 9-byte getStatus and READ
# the response.  This is the very first write in the test — nothing before it
# can dirty device state.

print("\n=== Phase 1: 9-byte getStatus (isolation test) ===")
print("  Hypothesis: 8-byte output reports ARE the correct transport.")
print("  Packet: [0x00, 0x01, 0x00×7]  →  expect opcode 0x81 in reply.")

phase1_ok = False
_p1_dev = _hid.device()
try:
    _p1_dev.open(VID, PID)
    _p1_dev.set_nonblocking(False)
    flush(_p1_dev)

    ret = write9(_p1_dev, 0x01)   # getStatus, no payload
    print(f"  write9(getStatus) → returned {ret}  (expect 9)")

    sleep(0.1)
    data = read64(_p1_dev, timeout_ms=500)

    if data is None:
        print("  read: TIMEOUT — device did not respond within 500 ms")
        check("9-byte getStatus → opcode 0x81", False, "read timeout")
    else:
        opcode = data[0]
        print(f"  read: {len(data)} bytes, opcode=0x{opcode:02X}")
        print(f"  response bytes [0:8]: {[hex(b) for b in data[:8]]}")
        phase1_ok = check("9-byte getStatus → opcode 0x81", opcode == 0x81,
                          f"got 0x{opcode:02X}")
        if opcode == 0x81:
            status_flags     = data[1]
            frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
            acq_active = bool(status_flags & 0x01)
            mem_full   = bool(status_flags & 0x02)
            print(f"  → acq_active={acq_active}  mem_full={mem_full}  "
                  f"framesInMemory={frames_in_memory}")
except Exception as e:
    check("Phase 1 raised no exception", False, str(e))
    traceback.print_exc()
finally:
    try:
        _p1_dev.close()
    except Exception:
        pass

# ── Phase 2: setAcquisitionParameters with uint16 exposureTime ───────────────
#
# libspec.py packs exposureTime as uint32 (4 bytes):
#   opcode(1) + numScans(2) + numBlankScans(2) + scanMode(1) + expTime(4) = 10 bytes
# The output report holds only 8 data bytes → last 2 bytes of expTime are lost.
#
# With uint16 (2 bytes):
#   opcode(1) + numScans(2) + numBlankScans(2) + scanMode(1) + expTime(2) = 8 bytes
#   → fits exactly.

print("\n=== Phase 2: setAcquisitionParameters with uint16 exposureTime ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=0, exposureTime=1000 (uint16)")

phase2_ok = False
_p2_dev = _hid.device()
try:
    _p2_dev.open(VID, PID)
    _p2_dev.set_nonblocking(False)
    flush(_p2_dev)

    # Verify device is alive before the real test
    write9(_p2_dev, 0x01)
    sleep(0.1)
    d = read64(_p2_dev, timeout_ms=300)
    if d is None or d[0] != 0x81:
        opstr = f"0x{d[0]:02X}" if d else "timeout"
        print(f"  Pre-check getStatus failed ({opstr}) — skipping Phase 2")
    else:
        # setAcquisitionParameters (opcode 0x03)
        # payload layout (7 bytes, fits in 8-byte output report):
        #   numOfScans(uint16) + numOfBlankScans(uint16) + scanMode(uint8)
        #   + exposureTime(uint16)
        payload  = list(struct.pack('<H', 1))     # numOfScans = 1
        payload += list(struct.pack('<H', 0))     # numOfBlankScans = 0
        payload += [0]                            # scanMode = 0 (Continuous)
        payload += list(struct.pack('<H', 1000))  # exposureTime = 1000 × 10µs = 10 ms
        # payload = 7 bytes → opcode(1) + payload(7) = 8 data bytes = exactly fills report

        print(f"  payload bytes: {[hex(b) for b in payload]}  (7 bytes)")
        ret = write9(_p2_dev, 0x03, payload)
        print(f"  write9(setAcqParams) → returned {ret}  (expect 9)")

        sleep(0.1)
        d2 = read64(_p2_dev, timeout_ms=500)
        if d2 is None:
            check("setAcquisitionParameters → opcode 0x83", False, "read timeout")
        else:
            op2 = d2[0]
            err = d2[1]
            print(f"  response: opcode=0x{op2:02X}, errorCode={err}")
            print(f"  response bytes [0:8]: {[hex(b) for b in d2[:8]]}")
            phase2_ok = check("setAcquisitionParameters → opcode 0x83",
                              op2 == 0x83, f"got 0x{op2:02X}")
            if op2 == 0x83:
                check("setAcquisitionParameters errorCode == 0", err == 0, f"got {err}")
except Exception as e:
    check("Phase 2 raised no exception", False, str(e))
    traceback.print_exc()
finally:
    try:
        _p2_dev.close()
    except Exception:
        pass

# ── Phase 3: Full acquisition sequence ───────────────────────────────────────
#
# Runs only if Phases 1 and 2 both passed.
# Uses 9-byte writes and uint16 exposureTime throughout.
# Does NOT use libspec.py — tests the raw HID protocol directly.

print("\n=== Phase 3: Full Acquisition Sequence ===")

if not (phase1_ok and phase2_ok):
    print(f"  Skipped — Phase 1 ok={phase1_ok}, Phase 2 ok={phase2_ok}")
else:
    _p3_dev = _hid.device()
    try:
        import numpy as np
        _p3_dev.open(VID, PID)
        _p3_dev.set_nonblocking(False)
        flush(_p3_dev)

        # 3a — getStatus ──────────────────────────────────────────────────────
        write9(_p3_dev, 0x01)
        sleep(0.1)
        d = read64(_p3_dev, timeout_ms=500)
        if not check("3a: getStatus → 0x81",
                     d is not None and d[0] == 0x81,
                     f"opcode=0x{d[0]:02X}" if d else "timeout"):
            raise RuntimeError("getStatus failed — cannot continue")
        frames_in_memory = struct.unpack_from('<H', bytes(d), 2)[0]
        mem_full = bool(d[1] & 0x02)
        print(f"     framesInMemory={frames_in_memory}  mem_full={mem_full}")

        if mem_full:
            write9(_p3_dev, 0x07)   # clearMemory
            sleep(0.05)
            d = read64(_p3_dev, timeout_ms=300)
            if d and d[0] == 0x87 and d[1] == 0:
                print("     clearMemory OK")
            else:
                opstr = f"0x{d[0]:02X} err={d[1]}" if d else "timeout"
                print(f"     clearMemory reply: {opstr}")

        # 3b — setAcquisitionParameters ───────────────────────────────────────
        payload  = list(struct.pack('<H', 1))     # numOfScans = 1
        payload += list(struct.pack('<H', 0))     # numOfBlankScans = 0
        payload += [0]                            # scanMode = 0 (Continuous)
        payload += list(struct.pack('<H', 1000))  # exposureTime = 1000 × 10µs
        write9(_p3_dev, 0x03, payload)
        sleep(0.1)
        d = read64(_p3_dev, timeout_ms=500)
        if not check("3b: setAcqParams → 0x83, err=0",
                     d is not None and d[0] == 0x83 and d[1] == 0,
                     f"opcode=0x{d[0]:02X} err={d[1]}" if d else "timeout"):
            raise RuntimeError("setAcquisitionParameters failed")

        # 3c — setFrameFormat ─────────────────────────────────────────────────
        ff_payload  = list(struct.pack('<H', 0))     # startElement = 0
        ff_payload += list(struct.pack('<H', 3647))  # endElement = 3647
        ff_payload += [0]                            # reductionMode = 0
        write9(_p3_dev, 0x04, ff_payload)
        sleep(0.1)
        d = read64(_p3_dev, timeout_ms=500)
        if not check("3c: setFrameFormat → 0x84, err=0",
                     d is not None and d[0] == 0x84 and d[1] == 0,
                     f"opcode=0x{d[0]:02X} err={d[1]}" if d else "timeout"):
            raise RuntimeError("setFrameFormat failed")
        num_pixels = struct.unpack_from('<H', bytes(d), 2)[0]
        print(f"     num_pixels={num_pixels}  (expect 3648)")
        check("3c: num_pixels == 3648", num_pixels == 3648, f"got {num_pixels}")

        # 3d — triggerAcquisition (no reply) ──────────────────────────────────
        write9(_p3_dev, 0x06)
        print("     triggerAcquisition sent")

        # 3e — poll getStatus until framesInMemory > 0 ────────────────────────
        frames_ready = 0
        for poll_n in range(20):
            sleep(0.05)
            write9(_p3_dev, 0x01)
            sleep(0.05)
            d = read64(_p3_dev, timeout_ms=300)
            if d and d[0] == 0x81:
                frames_ready = struct.unpack_from('<H', bytes(d), 2)[0]
                if frames_ready > 0:
                    print(f"     frame ready after {poll_n + 1} polls (~{(poll_n+1)*0.1:.1f} s)")
                    break
        if not check("3e: framesInMemory > 0 within 2 s", frames_ready > 0,
                     "still 0 after 20 polls — check scanMode / exposureTime"):
            raise RuntimeError("No frame arrived")

        # 3f — getFrame(0) ────────────────────────────────────────────────────
        packets_needed = math.ceil(num_pixels / 30)
        gf_payload  = [0x00, 0x00]                       # pixelOffset = 0
        gf_payload += list(struct.pack('<H', 0))         # frameIndex = 0
        gf_payload += [packets_needed]
        write9(_p3_dev, 0x0A, gf_payload)

        buf = np.zeros(3694, dtype=np.uint16)
        ok_frame = True
        for n in range(1, packets_needed + 1):
            d = read64(_p3_dev, timeout_ms=500)
            if d is None:
                print(f"     getFrame packet {n}/{packets_needed}: TIMEOUT")
                ok_frame = False
                break
            if d[0] != 0x8A:
                print(f"     getFrame packet {n}/{packets_needed}: "
                      f"wrong opcode 0x{d[0]:02X}")
                ok_frame = False
                break
            remaining = d[3]
            expected  = packets_needed - n
            if remaining >= 250 or remaining != expected:
                print(f"     getFrame packet {n}/{packets_needed}: "
                      f"remaining={remaining}, expected={expected}")
                ok_frame = False
                break
            pixel_offset = struct.unpack_from('<H', bytes(d), 1)[0]
            for i in range(30):
                idx = pixel_offset + i
                if idx >= num_pixels:
                    break
                buf[idx] = struct.unpack_from('<H', bytes(d), 4 + i * 2)[0]

        check("3f: getFrame(0) received all packets without error", ok_frame)
        if ok_frame:
            check("3f: buf contains non-zero values",
                  np.any(buf > 0),
                  "all zeros — exposure too short or check ambient light")
            print(f"     buf[32:36] = {list(buf[32:36])}  (first active pixels)")
            print(f"     buf max={int(buf.max())}  min={int(buf.min())}")

        # 3g — readFlash first 100 bytes ──────────────────────────────────────
        PAYLOAD_SIZE = 60
        total_pkts = math.ceil(100 / PAYLOAD_SIZE)   # = 2
        rf_payload  = list(struct.pack('<I', 0))      # flashOffset = 0
        rf_payload += [total_pkts]                    # burstCount
        write9(_p3_dev, 0x1A, rf_payload)

        flash_buf = bytearray(100)
        ok_flash = True
        for burst_n in range(1, total_pkts + 1):
            d = read64(_p3_dev, timeout_ms=500)
            if d is None:
                print(f"     readFlash packet {burst_n}/{total_pkts}: TIMEOUT")
                ok_flash = False
                break
            if d[0] != 0x9A:
                print(f"     readFlash packet {burst_n}/{total_pkts}: "
                      f"wrong opcode 0x{d[0]:02X}")
                ok_flash = False
                break
            remaining = d[3]
            expected  = total_pkts - burst_n
            if remaining >= 250 or remaining != expected:
                print(f"     readFlash packet {burst_n}/{total_pkts}: "
                      f"remaining={remaining}, expected={expected}")
                ok_flash = False
                break
            local_offset = struct.unpack_from('<H', bytes(d), 1)[0]
            for i in range(PAYLOAD_SIZE):
                buf_idx = local_offset + i
                if buf_idx >= 100:
                    break
                flash_buf[buf_idx] = d[4 + i]

        check("3g: readFlash(100 bytes) received all packets", ok_flash)
        if ok_flash:
            snippet = flash_buf.decode("utf-8", errors="replace")
            print(f"     first 60 chars of flash: {snippet[:60]!r}")

    except RuntimeError as e:
        print(f"  Phase 3 aborted: {e}")
    except Exception as e:
        check("Phase 3 raised no unexpected exception", False, str(e))
        traceback.print_exc()
    finally:
        try:
            _p3_dev.close()
        except Exception:
            pass

# ── Phase 4: Feature report ID scan (fallback) ────────────────────────────────
#
# Only runs if Phase 1 failed.  Scans report IDs 0x01–0x07 to see if any
# feature report channel accepts the getStatus command.

print("\n=== Phase 4: Feature Report ID Scan (fallback) ===")
if phase1_ok:
    print("  Phase 1 passed — 9-byte writes work.  Skipping feature report scan.")
else:
    print("  Phase 1 failed.  Scanning feature report IDs 0x01–0x07...")
    _p4_dev = _hid.device()
    try:
        _p4_dev.open(VID, PID)
        _p4_dev.set_nonblocking(False)
        found_id = None
        for rid in range(0x01, 0x08):
            flush(_p4_dev, max_reads=5)
            # send_feature_report: first byte is report ID, then opcode 0x01, rest zeros
            pkt = [rid, 0x01] + [0x00] * 63   # 65 bytes total
            try:
                ret = _p4_dev.send_feature_report(pkt)
                sleep(0.1)
                raw = _p4_dev.read(65, 300)
                if raw:
                    # normalize
                    if len(raw) == 65 and raw[0] == 0x00:
                        opcode = raw[1]
                    else:
                        opcode = raw[0]
                    ok = opcode == 0x81
                    print(f"  report ID 0x{rid:02X}: send→{ret:3d}  "
                          f"reply opcode=0x{opcode:02X}  {'← WORKS' if ok else ''}")
                    if ok and found_id is None:
                        found_id = rid
                else:
                    print(f"  report ID 0x{rid:02X}: send→{ret:3d}  read=TIMEOUT")
            except Exception as fe:
                print(f"  report ID 0x{rid:02X}: exception — {fe}")
        if found_id is not None:
            check(f"feature report ID 0x{found_id:02X} replies 0x81", True)
            print(f"\n  → libspec.py _write() should use send_feature_report([0x{found_id:02X}, opcode, ...])")
        else:
            check("at least one feature report ID replies 0x81", False,
                  "no working feature report ID found")
    except Exception as e:
        print(f"  Phase 4 setup error: {e}")
        traceback.print_exc()
    finally:
        try:
            _p4_dev.close()
        except Exception:
            pass

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 55)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")

if phase1_ok and phase2_ok:
    print("""
  CONCLUSION: 9-byte writes + uint16 exposureTime work.

  Required changes to libspec.py:

    1. _write()  (~line 90):
         BEFORE: pkt += [0x00] * (65 - len(pkt))
                 written = self._dev.write(pkt)
                 if written < 0:
         AFTER:  pkt = pkt[:9]
                 pkt += [0x00] * (9 - len(pkt))
                 written = self._dev.write(pkt)
                 if written != 9:

    2. configure_acquisition()  (~line 250):
         BEFORE: payload += list(struct.pack('<I', self.exposure_time))
         AFTER:  payload += list(struct.pack('<H', self.exposure_time))""")
elif not phase1_ok:
    print("""
  CONCLUSION: 9-byte writes do NOT return 0x81.
  The output report transport is broken.
  See Phase 4 feature report scan results above for next steps.""")
else:
    print("""
  CONCLUSION: 9-byte writes work but setAcquisitionParameters failed.
  Check the Phase 2 response bytes — the uint16 encoding may be wrong,
  or scanMode=0 may not be valid in the current device state.""")

if failed == 0:
    print("\n  All tests passed.")

print("=" * 55 + "\n")

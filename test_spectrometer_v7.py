"""
Diagnostic test for ASQE Spectrometer — version 7.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v7.py

v6 outcome
----------
  Phase 1 PASSED: get_status() works.
  Phase 2 FAILED: setAcquisitionParameters returned error code 13 (0x0D).
  Phase 3 FROZE:  capture_frame() polled framesInMemory forever because Phase 2
                  failed and no acquisition was ever configured.

Root question: what is the correct byte layout for setAcquisitionParameters?

Error code 13 could mean:
  - scan_mode=3 is rejected in the current device state
  - numOfScans/numOfBlankScans are uint8, not uint16 → wrong field alignment
  - Something else is out of range

This test
---------
  Sends setAcquisitionParameters as a raw 65-byte write (→ 9 bytes on Windows)
  with 4 different byte-layout variants.  Prints the full raw response for each.
  The variant returning error_code = 0 is the correct protocol layout.

  All variants use: numOfScans=1, numOfBlankScans=0, exposureTime=1000 (10 ms)

  Variant A  uint16 counts, scan_mode=0
             [0x03, 0x01,0x00, 0x00,0x00, 0x00, 0xE8,0x03]

  Variant B  uint16 counts, scan_mode=3  ← current libspec.py layout (fails)
             [0x03, 0x01,0x00, 0x00,0x00, 0x03, 0xE8,0x03]

  Variant C  uint8 counts,  scan_mode=0
             [0x03, 0x01, 0x00, 0x00, 0xE8,0x03, 0x00,0x00]

  Variant D  uint8 counts,  scan_mode=3
             [0x03, 0x01, 0x00, 0x03, 0xE8,0x03, 0x00,0x00]

  After identifying the correct variant, Phase 2 attempts setFrameFormat, and
  Phase 3 does a full capture (only reached if Phases 1+2 succeed).
"""

import sys
import struct
import traceback
from time import sleep

# ── Helpers ────────────────────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


def write65(dev, opcode, payload=None):
    """Send command as a 65-byte zero-padded packet (matches libspec.py _write)."""
    pkt = [0x00, opcode] + (payload or [])
    pkt += [0x00] * (65 - len(pkt))
    return dev.write(pkt)


def read_norm(dev, timeout_ms=300):
    """Read one packet and strip the 0x0D report-ID prefix if present."""
    raw = dev.read(65, timeout_ms)
    if not raw:
        return None
    data = list(raw)
    if data and data[0] == 0x0D:
        data = data[1:]
    return data


def hexdump(data, indent="    "):
    if not data:
        print(f"{indent}(empty)")
        return
    for row in range(0, len(data), 16):
        chunk = data[row:row + 16]
        hex_part = " ".join(f"{x:02X}" for x in chunk)
        asc_part = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"{indent}{row:04X}  {hex_part:<48}  {asc_part}")


def drain(dev, timeout_ms=100, max_reads=5):
    for _ in range(max_reads):
        if not dev.read(65, timeout_ms):
            break


# ── Device open ────────────────────────────────────────────────────────────────

VID, PID = 0x20E2, 0x0001

print("\n=== Pre-flight ===")
try:
    import hid as _hid
    check("hid importable", True)
except ImportError as e:
    check("hid importable", False, str(e))
    sys.exit(1)

devs = _hid.enumerate(VID, PID)
if not check(f"device visible (VID=0x{VID:04X} PID=0x{PID:04X})", len(devs) > 0):
    sys.exit(1)

dev = _hid.device()
dev.open(VID, PID)
dev.set_nonblocking(False)
drain(dev)

# ── Phase 0: Sanity — getStatus ────────────────────────────────────────────────

print("\n=== Phase 0: getStatus sanity check ===")
write65(dev, 0x01)
data = read_norm(dev, 300)
if data is None:
    check("getStatus responds", False, "timeout")
else:
    ok = (data[0] == 0x01)
    check("getStatus cmd echo = 0x01", ok, f"got 0x{data[0]:02X}" if not ok else "")
    print(f"  statusFlags=0x{data[1]:02X}  framesInMemory={struct.unpack_from('<H', bytes(data), 2)[0]}")
drain(dev)

# ── Phase 1: setAcquisitionParameters variants ────────────────────────────────
# Try 4 byte-layout variants; identify which one the device accepts.

print("\n=== Phase 1: setAcquisitionParameters byte-layout probe ===")
print("  All variants: numOfScans=1, numOfBlankScans=0, exposureTime=1000")
print()

# Build variant payloads (everything after opcode = 0x03)
VARIANTS = [
    ("A", "uint16 counts, scan_mode=0",
     [0x01, 0x00,  # numOfScans=1  (uint16 LE)
      0x00, 0x00,  # numOfBlankScans=0 (uint16 LE)
      0x00,        # scanMode=0
      0xE8, 0x03]  # exposureTime=1000 (uint16 LE)
    ),
    ("B", "uint16 counts, scan_mode=3  ← current libspec.py layout",
     [0x01, 0x00,  # numOfScans=1  (uint16 LE)
      0x00, 0x00,  # numOfBlankScans=0 (uint16 LE)
      0x03,        # scanMode=3
      0xE8, 0x03]  # exposureTime=1000 (uint16 LE)
    ),
    ("C", "uint8  counts, scan_mode=0",
     [0x01,        # numOfScans=1  (uint8)
      0x00,        # numOfBlankScans=0 (uint8)
      0x00,        # scanMode=0
      0xE8, 0x03, 0x00, 0x00]  # exposureTime=1000 (uint32 LE)
    ),
    ("D", "uint8  counts, scan_mode=3",
     [0x01,        # numOfScans=1  (uint8)
      0x00,        # numOfBlankScans=0 (uint8)
      0x03,        # scanMode=3
      0xE8, 0x03, 0x00, 0x00]  # exposureTime=1000 (uint32 LE)
    ),
]

working_variant = None
for (tag, desc, payload) in VARIANTS:
    drain(dev)
    print(f"  Variant {tag}: {desc}")
    print(f"  packet data bytes: {[f'0x{b:02X}' for b in [0x03] + payload]}")

    write65(dev, 0x03, payload)
    data = read_norm(dev, 300)

    if data is None:
        print("  response: TIMEOUT")
        check(f"variant {tag}: response received", False, "timeout")
    else:
        print(f"  response bytes [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
        hexdump(data[:16])
        cmd_ok  = (data[0] == 0x03)
        err_ok  = (data[1] == 0x00)
        check(f"variant {tag}: cmd echo = 0x03", cmd_ok,
              f"got 0x{data[0]:02X}" if not cmd_ok else "")
        check(f"variant {tag}: error_code = 0 (success)", err_ok,
              f"error code {data[1]}" if not err_ok else "")
        if cmd_ok and err_ok and working_variant is None:
            working_variant = (tag, desc, payload)
            print(f"  *** Variant {tag} SUCCEEDED — this is the correct layout ***")
    print()
    sleep(0.05)

# ── Phase 2: setFrameFormat (only if a working variant was found) ─────────────

print("=== Phase 2: setFrameFormat ===")
if working_variant is None:
    print("  skipped — no working setAcquisitionParameters variant found")
    _results.append(("setFrameFormat", False))
else:
    drain(dev)
    # setFrameFormat: startElement=0 (uint16), endElement=3647=0x0E3F (uint16), reductionMode=0
    sf_payload = [0x00, 0x00,   # startElement=0
                  0x3F, 0x0E,   # endElement=3647 (LE)
                  0x00]         # reductionMode=0
    print(f"  packet data bytes: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
    write65(dev, 0x04, sf_payload)
    data = read_norm(dev, 300)
    if data is None:
        check("setFrameFormat: response received", False, "timeout")
    else:
        print(f"  response bytes [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
        hexdump(data[:16])
        cmd_ok = (data[0] == 0x04)
        err_ok = (data[1] == 0x00)
        check("setFrameFormat: cmd echo = 0x04", cmd_ok,
              f"got 0x{data[0]:02X}" if not cmd_ok else "")
        check("setFrameFormat: error_code = 0 (success)", err_ok,
              f"error code {data[1]}" if not err_ok else "")
        if cmd_ok and err_ok:
            num_pixels = struct.unpack_from('<H', bytes(data), 2)[0]
            print(f"  numPixelsInFrame = {num_pixels}  (expect 3648)")

# ── Phase 3: trigger + getStatus poll (only if Phases 0-2 passed) ─────────────

phase2_ok = all(ok for (lbl, ok) in _results if "setFrameFormat" in lbl)

print("\n=== Phase 3: triggerAcquisition + poll ===")
if working_variant is None or not phase2_ok:
    print("  skipped — Phases 1/2 did not fully pass")
    _results.append(("trigger + poll", False))
else:
    drain(dev)
    print("  sending triggerAcquisition (0x06) — no reply expected")
    write65(dev, 0x06)

    frames = 0
    for poll in range(200):   # max 5 s (200 × 25 ms)
        sleep(0.025)
        write65(dev, 0x01)
        data = read_norm(dev, 200)
        if data and data[0] == 0x01:
            frames = struct.unpack_from('<H', bytes(data), 2)[0]
            if frames > 0:
                print(f"  framesInMemory = {frames} after {(poll+1)*25} ms")
                break
    else:
        print("  framesInMemory never > 0 after 5 s")

    check("frame ready within 5 s", frames > 0)

# ── Cleanup + summary ──────────────────────────────────────────────────────────

try:
    dev.close()
except Exception:
    pass

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
if working_variant:
    print(f"\n  Correct setAcquisitionParameters layout: Variant {working_variant[0]}")
    print(f"  Description: {working_variant[1]}")
    print(f"  → Update libspec.py configure_acquisition() to match this layout.")
else:
    print("\n  No working layout found — inspect hex dumps above.")
print("=" * 60 + "\n")

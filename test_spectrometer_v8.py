"""
Diagnostic test for ASQE Spectrometer — version 8.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v8.py

v7 outcome
----------
  All 4 byte-layout variants of setAcquisitionParameters returned the same response:
    03 0D 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E
  Device is freshly plugged in each run; integration time 10ms is within limits.

Root question revised
---------------------
  v7 gated Phases 2 and 3 on error_code == 0, so the full pipeline was NEVER tested
  past setAcquisitionParameters. But byte[1] being an error code is an assumption —
  for getStatus, byte[1] is statusFlags, not an error code. The identical response
  across all 4 variants suggests 0x0D may be a non-error status byte specific to
  the setAcquisitionParameters response format.

This test
---------
  Proceeds unconditionally through all phases regardless of byte[1] values.

  Phase 0  getStatus baseline.

  Phase 1  setAcquisitionParameters — record byte[1] but do NOT gate on it.
           numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000 (50ms).

  Phase 2  setFrameFormat — record byte[1] but do NOT gate on it.
           startElement=0, endElement=3647, reductionMode=0.

  Phase 3  triggerAcquisition + poll getStatus for up to 5 s.
           If framesInMemory > 0, attempt getFrame(0xFFFF) and print pixel samples.

Interpretation
--------------
  Phase 3 PASS  → byte[1]=0x0D is non-fatal; libspec.py error check at line 253 is wrong.
  Phase 3 FAIL  → commands are genuinely rejected; inspect per-phase byte[1] values.
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
    """Send command as a 65-byte zero-padded packet."""
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


def hexdump(data, indent="    ", max_bytes=16):
    if not data:
        print(f"{indent}(empty)")
        return
    chunk = data[:max_bytes]
    hex_part = " ".join(f"{x:02X}" for x in chunk)
    asc_part = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
    print(f"{indent}{hex_part:<48}  {asc_part}")


def drain(dev, timeout_ms=100, max_reads=8):
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

# ── Phase 0: getStatus baseline ───────────────────────────────────────────────

print("\n=== Phase 0: getStatus baseline ===")
write65(dev, 0x01)
data = read_norm(dev, 300)
if data is None:
    check("getStatus responds", False, "timeout")
    sys.exit(1)

cmd_ok = (data[0] == 0x01)
check("getStatus cmd echo = 0x01", cmd_ok, f"got 0x{data[0]:02X}" if not cmd_ok else "")
status_flags = data[1]
frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
print(f"  statusFlags=0x{status_flags:02X}  framesInMemory={frames_in_memory}")
hexdump(data)
drain(dev)

# ── Phase 1: setAcquisitionParameters — proceed regardless of byte[1] ─────────

print("\n=== Phase 1: setAcquisitionParameters ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000 (50 ms)")
print("  Layout: [numOfScans:u16, numOfBlankScans:u16, scanMode:u8, exposureTime:u32]")

acq_payload = list(struct.pack('<H', 1))     # numOfScans=1
acq_payload += list(struct.pack('<H', 0))    # numOfBlankScans=0
acq_payload += [3]                           # scanMode=3
acq_payload += list(struct.pack('<I', 5000)) # exposureTime=5000

print(f"  packet bytes: {[f'0x{b:02X}' for b in [0x03] + acq_payload]}")
write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)

if data is None:
    check("setAcquisitionParameters response", False, "timeout")
    print("  *** TIMEOUT — cannot proceed ***")
    sys.exit(1)

print(f"  raw response bytes [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
hexdump(data)
p1_cmd_ok = (data[0] == 0x03)
p1_byte1 = data[1]
check("setAcquisitionParameters cmd echo = 0x03", p1_cmd_ok,
      f"got 0x{data[0]:02X}" if not p1_cmd_ok else "")
print(f"  byte[1] = 0x{p1_byte1:02X} ({p1_byte1})  "
      f"{'← 0 = success' if p1_byte1 == 0 else '← non-zero (treating as non-fatal, proceeding)'}")
drain(dev)

# ── Phase 2: setFrameFormat — proceed regardless of byte[1] ───────────────────

print("\n=== Phase 2: setFrameFormat ===")
sf_payload  = list(struct.pack('<H', 0))     # startElement=0
sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
sf_payload += [0]                            # reductionMode=0

print(f"  packet bytes: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
write65(dev, 0x04, sf_payload)
data = read_norm(dev, 500)

if data is None:
    check("setFrameFormat response", False, "timeout")
    print("  *** TIMEOUT — cannot proceed ***")
    sys.exit(1)

print(f"  raw response bytes [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
hexdump(data)
p2_cmd_ok = (data[0] == 0x04)
p2_byte1 = data[1]
check("setFrameFormat cmd echo = 0x04", p2_cmd_ok,
      f"got 0x{data[0]:02X}" if not p2_cmd_ok else "")
print(f"  byte[1] = 0x{p2_byte1:02X} ({p2_byte1})  "
      f"{'← 0 = success' if p2_byte1 == 0 else '← non-zero (proceeding regardless)'}")

# Try to read numPixelsInFrame from byte[2:4] in case it's meaningful
if len(data) >= 4:
    num_pixels = struct.unpack_from('<H', bytes(data), 2)[0]
    print(f"  bytes[2:4] interpreted as numPixelsInFrame = {num_pixels}"
          f"  {'(expect 3648)' if num_pixels == 3648 else ''}")
drain(dev)

# ── Phase 3: triggerAcquisition + poll ────────────────────────────────────────

print("\n=== Phase 3: triggerAcquisition + poll ===")
print("  sending triggerAcquisition (0x06) — no reply expected")
write65(dev, 0x06)

frames = 0
poll_count = 0
for poll in range(200):   # max 5 s (200 × 25 ms)
    sleep(0.025)
    write65(dev, 0x01)
    status_data = read_norm(dev, 200)
    if status_data and status_data[0] == 0x01:
        poll_count = poll + 1
        frames = struct.unpack_from('<H', bytes(status_data), 2)[0]
        acq_active = bool(status_data[1] & 0x01)
        if frames > 0 or poll % 20 == 0:
            print(f"  poll {poll_count:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{status_data[1]:02X}  framesInMemory={frames}"
                  f"  acq_active={acq_active}")
        if frames > 0:
            break
else:
    print(f"  framesInMemory never > 0 after 5 s  (last statusFlags=0x{status_data[1]:02X})")

frame_ready = check("frame ready within 5 s", frames > 0)

# ── Phase 4: getFrame (only if frame ready) ───────────────────────────────────

if frame_ready:
    print("\n=== Phase 4: getFrame(0xFFFF) ===")
    # Use 3648 pixels (full range); ceil(3648/30) = 122 packets
    num_pixels = 3648
    packets_needed = (num_pixels + 29) // 30  # = 122

    frame_payload  = list(struct.pack('<H', 0))         # pixelOffset=0
    frame_payload += list(struct.pack('<H', 0xFFFF))    # frame index 0xFFFF
    frame_payload += [packets_needed]

    print(f"  requesting {packets_needed} packets for {num_pixels} pixels")
    write65(dev, 0x0A, frame_payload)

    import array as _array
    buf = [0] * 3694
    ok = True
    for n in range(1, packets_needed + 1):
        pkt = read_norm(dev, 200)
        if pkt is None or pkt[0] != 0x0A:
            got = f"0x{pkt[0]:02X}" if pkt else "timeout"
            print(f"  packet {n}: bad reply {got}")
            ok = False
            break
        remaining = pkt[3]
        expected  = packets_needed - n
        if remaining != expected:
            print(f"  packet {n}: remaining={remaining} expected={expected}")
            ok = False
            break
        pixel_offset = struct.unpack_from('<H', bytes(pkt), 1)[0]
        for i in range(30):
            idx = pixel_offset + i
            if idx >= num_pixels:
                break
            buf[idx] = struct.unpack_from('<H', bytes(pkt), 4 + i * 2)[0]

    if ok:
        check("getFrame: all packets received", True)
        nonzero = sum(1 for v in buf[:num_pixels] if v > 0)
        print(f"  first 8 pixels: {buf[:8]}")
        print(f"  pixels 1820:1828: {buf[1820:1828]}")
        print(f"  non-zero pixels: {nonzero} / {num_pixels}")
        check("getFrame: non-zero pixel data", nonzero > 0)
    else:
        check("getFrame: all packets received", False)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
print(f"  setAcquisitionParameters byte[1] = 0x{p1_byte1:02X} ({p1_byte1})")
print(f"  setFrameFormat          byte[1] = 0x{p2_byte1:02X} ({p2_byte1})")
if frame_ready:
    print()
    print("  *** FRAME CAPTURED — byte[1] is NOT a blocking error code ***")
    print("  Fix: remove/adjust the error check in libspec.py configure_acquisition()")
else:
    print()
    print("  No frame captured — commands may be genuinely rejected.")
    print("  Inspect the byte[1] values and raw response bytes above.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

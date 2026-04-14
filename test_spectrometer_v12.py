"""
Diagnostic test for ASQE Spectrometer — version 12.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v12.py

v11 outcome
-----------
  setAcquisitionParameters IS taking effect: getStatus bytes[4] and [6]
  changed from 0x00 to 0x01 after the call.  byte[1]=0x0D is a non-error
  ACK field.  After triggerAcquisition, byte[4] returned to 0x00, suggesting
  the trigger was accepted.

  Still no frames: framesInMemory stayed 0 throughout the 5 s poll.

Root cause hypothesis
---------------------
  In v11 we SKIPPED setFrameFormat entirely.  The device may need it to know
  the pixel range before it can store a frame — even though setFrameFormat
  (0x04) is write-only (no response).  Without the frame format, the trigger
  is accepted but no frame is built.

  Also switching from scanMode=3 (Frame Averaging) to scanMode=0 (Continuous)
  for simplicity: one trigger stores one frame, retrieved with getFrame(0).

This test
---------
  Phase 0  getStatus (pre-configuration baseline).

  Phase 1  setAcquisitionParameters: numOfScans=1, numOfBlankScans=0,
           scanMode=0, exposureTime=5000 (50 ms).

  Phase 2  getStatus after — verify bytes[4,6] changed (params registered).

  Phase 3  setFrameFormat: startElement=0, endElement=3647, reductionMode=0.
           WRITE-ONLY — send packet, do NOT read a response.

  Phase 4  Brief sleep(0.1) to let the device process both commands.

  Phase 5  triggerAcquisition (write-only) + poll framesInMemory.

  Phase 6  getFrame(0) — frameIndex=0 for scanMode=0 (first stored frame).
"""

import sys
import struct
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
    pkt = [0x00, opcode] + (payload or [])
    pkt += [0x00] * (65 - len(pkt))
    return dev.write(pkt)


def read_norm(dev, timeout_ms=500):
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


def drain(dev, timeout_ms=150, max_reads=6):
    for _ in range(max_reads):
        if not dev.read(65, timeout_ms):
            break


def get_status(dev):
    write65(dev, 0x01)
    return read_norm(dev, 500)


# ── Device open ────────────────────────────────────────────────────────────────

VID, PID = 0x20E2, 0x0001

print("\n=== Pre-flight ===")
try:
    import hid as _hid
    check("hid importable", True)
except ImportError as e:
    check("hid importable", False, str(e)); sys.exit(1)

devs = _hid.enumerate(VID, PID)
if not check(f"device visible (VID=0x{VID:04X} PID=0x{PID:04X})", len(devs) > 0):
    sys.exit(1)

dev = _hid.device()
dev.open(VID, PID)
dev.set_nonblocking(False)
drain(dev)

# ── Phase 0: getStatus baseline ───────────────────────────────────────────────

print("\n=== Phase 0: getStatus baseline ===")
status0 = get_status(dev)
if status0 is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", status0[0] == 0x01)
print(f"  statusFlags=0x{status0[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(status0),2)[0]}")
hexdump(status0)
drain(dev)

# ── Phase 1: setAcquisitionParameters ─────────────────────────────────────────

print("\n=== Phase 1: setAcquisitionParameters ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=0, exposureTime=5000 (50 ms)")

acq_payload  = list(struct.pack('<H', 1))      # numOfScans=1
acq_payload += list(struct.pack('<H', 0))      # numOfBlankScans=0
acq_payload += [0]                             # scanMode=0  ← Continuous mode
acq_payload += list(struct.pack('<I', 5000))   # exposureTime=5000

write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    print("  TIMEOUT — no response")
else:
    check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03,
          f"got 0x{data[0]:02X}" if data[0] != 0x03 else "")
    print(f"  byte[1]=0x{data[1]:02X}  (non-fatal ACK field)")
drain(dev)

# ── Phase 2: getStatus post-setAcquisitionParameters ─────────────────────────

print("\n=== Phase 2: getStatus (post-setAcquisitionParameters) ===")
status2 = get_status(dev)
if status2:
    print(f"  statusFlags=0x{status2[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(status2),2)[0]}")
    hexdump(status2)
    changed = [(i, status0[i], status2[i]) for i in range(min(len(status0), len(status2)))
               if status0[i] != status2[i]]
    if changed:
        print("  CHANGED bytes vs baseline: " +
              ", ".join(f"[{i}]: 0x{a:02X}→0x{b:02X}" for i, a, b in changed))
    else:
        print("  No bytes changed — setAcquisitionParameters may not have taken effect!")
drain(dev)

# ── Phase 3: setFrameFormat — write-only (no read) ───────────────────────────

print("\n=== Phase 3: setFrameFormat (write-only) ===")
sf_payload  = list(struct.pack('<H', 0))     # startElement=0
sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
sf_payload += [0]                            # reductionMode=0
print(f"  sending 0x04 packet (no response expected)")
print(f"  packet: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
write65(dev, 0x04, sf_payload)
# DO NOT read — write-only command

# ── Phase 4: brief settle ─────────────────────────────────────────────────────

print("\n=== Phase 4: settle (100 ms) + drain ===")
sleep(0.1)
drained = 0
for _ in range(6):
    r = dev.read(65, 50)
    if not r:
        break
    drained += 1
    print(f"  drained packet: {[f'0x{x:02X}' for x in list(r)[:8]]}")
print(f"  drained {drained} packet(s)")

# ── Phase 5: triggerAcquisition + poll ────────────────────────────────────────

print("\n=== Phase 5: triggerAcquisition + poll ===")
print("  sending triggerAcquisition (0x06)")
write65(dev, 0x06)

frames = 0
last_sd = None
acq_was_active = False
for poll in range(200):
    sleep(0.025)
    sd = get_status(dev)
    if sd and sd[0] == 0x01:
        last_sd = sd
        frames = struct.unpack_from('<H', bytes(sd), 2)[0]
        acq_active = bool(sd[1] & 0x01)
        if acq_active and not acq_was_active:
            print(f"  poll {poll+1:3d}: acq_active went TRUE")
            acq_was_active = True
        if frames > 0 or poll % 20 == 0:
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}  "
                  f"acq_active={acq_active}")
        if frames > 0:
            break
else:
    print("  framesInMemory never > 0 after 5 s")
    if last_sd:
        hexdump(last_sd)

check("frame ready within 5 s", frames > 0)

# ── Phase 6: getFrame(0) ──────────────────────────────────────────────────────

if frames > 0:
    print("\n=== Phase 6: getFrame(frameIndex=0) ===")
    NUM_PIXELS = 3648
    packets_needed = (NUM_PIXELS + 29) // 30  # = 122

    fp  = list(struct.pack('<H', 0))       # pixelOffset=0
    fp += list(struct.pack('<H', 0))       # frameIndex=0  (first stored frame)
    fp += [packets_needed]

    print(f"  requesting {packets_needed} packets for {NUM_PIXELS} pixels")
    write65(dev, 0x0A, fp)

    buf = [0] * 3694
    ok = True
    for n in range(1, packets_needed + 1):
        pkt = read_norm(dev, 300)
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
            if idx >= NUM_PIXELS:
                break
            buf[idx] = struct.unpack_from('<H', bytes(pkt), 4 + i * 2)[0]

    if ok:
        check("getFrame: all packets received", True)
        nonzero = sum(1 for v in buf[:NUM_PIXELS] if v > 0)
        print(f"  first 8 pixels:   {buf[:8]}")
        print(f"  pixels 1820:1828: {buf[1820:1828]}")
        print(f"  non-zero pixels:  {nonzero} / {NUM_PIXELS}")
        check("getFrame: non-zero pixel data", nonzero > 0)
    else:
        check("getFrame: all packets received", False)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
if frames > 0:
    print("  *** FRAME CAPTURED ***")
    print("  setFrameFormat is write-only (no response).")
    print("  Fix libspec.py configure_acquisition():")
    print("    1. Remove error check on setAcquisitionParameters byte[1]")
    print("    2. Change setFrameFormat to write-only (_write instead of _write_read)")
    print("    3. Hardcode _num_pixels_in_frame = num_end - num_start + 1")
else:
    print("  No frame captured.")
    print("  Phase 3 drain shows whether setFrameFormat generated a delayed response.")
    print("  Phase 2 — did setAcquisitionParameters register (bytes changed)?")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

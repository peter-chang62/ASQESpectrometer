"""
Diagnostic test for ASQE Spectrometer — version 11.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v11.py

v10 outcome
-----------
  getFrameFormat (0x08): responds with cmd_echo + fill pattern (same as
    setAcquisitionParameters).  No real data payload — just ACK.
  setFrameFormat (0x04): NO response (write-only command, like clearMemory).

Pattern identified
------------------
  Responding with ACK (cmd_echo + fill):  0x01 (real data), 0x03, 0x08
  Write-only / no response:               0x04, 0x06, 0x07

  libspec.py has always called _write_read(0x04, ...) for setFrameFormat,
  blocking on a response that never comes.  This is why the pipeline stalls.

  The device may use its current (or default) frame format automatically.
  Factory default: startElement=0, endElement=3647, reductionMode=0 → 3648 pixels.

This test
---------
  Phase 0  getStatus (pre-configuration baseline).

  Phase 1  setAcquisitionParameters (scanMode=3, exposureTime=5000).
           Accept any ACK, do not gate.

  Phase 2  getStatus again — compare bytes[4:] to Phase 0 to see if
           the device registered the new parameters.

  Phase 3  SKIP setFrameFormat.  Use hardcoded numPixels=3648 for getFrame.

  Phase 4  triggerAcquisition + poll framesInMemory.

  Phase 5  getFrame(0xFFFF) if frame ready.
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
    """Send getStatus and return the data list, or None on timeout."""
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

# ── Phase 0: getStatus pre-configuration baseline ─────────────────────────────

print("\n=== Phase 0: getStatus (pre-configuration) ===")
status0 = get_status(dev)
if status0 is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", status0[0] == 0x01)
print(f"  statusFlags=0x{status0[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(status0),2)[0]}")
print(f"  full response (baseline):")
hexdump(status0)
drain(dev)

# ── Phase 1: setAcquisitionParameters ─────────────────────────────────────────

print("\n=== Phase 1: setAcquisitionParameters ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000 (50 ms)")

acq_payload  = list(struct.pack('<H', 1))      # numOfScans=1
acq_payload += list(struct.pack('<H', 0))      # numOfBlankScans=0
acq_payload += [3]                             # scanMode=3
acq_payload += list(struct.pack('<I', 5000))   # exposureTime=5000

write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    print("  TIMEOUT — no response")
else:
    print(f"  response: {[f'0x{b:02X}' for b in data[:8]]}")
    check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03,
          f"got 0x{data[0]:02X}" if data[0] != 0x03 else "")
    print(f"  byte[1] = 0x{data[1]:02X}  ← treating as non-fatal ACK field")
drain(dev)

# ── Phase 2: getStatus post-configuration — did device register the params? ───

print("\n=== Phase 2: getStatus (post-setAcquisitionParameters) ===")
status2 = get_status(dev)
if status2 is None:
    print("  TIMEOUT")
else:
    print(f"  statusFlags=0x{status2[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(status2),2)[0]}")
    print(f"  full response (post-config):")
    hexdump(status2)
    # Compare to baseline
    changed = [(i, status0[i], status2[i]) for i in range(min(len(status0), len(status2)))
               if status0[i] != status2[i]]
    if changed:
        print(f"  CHANGED bytes vs baseline: " +
              ", ".join(f"[{i}]: 0x{a:02X}→0x{b:02X}" for i, a, b in changed))
    else:
        print("  No bytes changed vs baseline.")
drain(dev)

# ── Phase 3: setFrameFormat SKIPPED ───────────────────────────────────────────

print("\n=== Phase 3: setFrameFormat — SKIPPED ===")
print("  setFrameFormat (0x04) is write-only (no response).")
print("  Assuming device uses default frame format: full range, 3648 pixels.")
NUM_PIXELS = 3648

# ── Phase 4: triggerAcquisition + poll ────────────────────────────────────────

print("\n=== Phase 4: triggerAcquisition + poll ===")
print("  sending triggerAcquisition (0x06) — write-only, no response")
write65(dev, 0x06)

frames = 0
last_sd = None
for poll in range(200):
    sleep(0.025)
    sd = get_status(dev)
    if sd and sd[0] == 0x01:
        last_sd = sd
        frames = struct.unpack_from('<H', bytes(sd), 2)[0]
        acq_active = bool(sd[1] & 0x01)
        if frames > 0 or poll % 20 == 0:
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}  "
                  f"acq_active={acq_active}")
        if frames > 0:
            break
else:
    print("  framesInMemory never > 0 after 5 s")
    if last_sd:
        print("  final getStatus response:")
        hexdump(last_sd)

check("frame ready within 5 s", frames > 0)

# ── Phase 5: getFrame(0xFFFF) ─────────────────────────────────────────────────

if frames > 0:
    print("\n=== Phase 5: getFrame(0xFFFF) ===")
    packets_needed = (NUM_PIXELS + 29) // 30   # = 122 for 3648 pixels

    fp  = list(struct.pack('<H', 0))         # pixelOffset=0
    fp += list(struct.pack('<H', 0xFFFF))    # frameIndex=0xFFFF
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
    print("  Fix libspec.py: remove _write_read for setFrameFormat;")
    print("  send it write-only and hardcode _num_pixels_in_frame=3648.")
else:
    print("  No frame — check Phase 2 output:")
    print("  Did getStatus change after setAcquisitionParameters?")
    print("  If yes: device accepted params but trigger/getFrame pipeline needs fixing.")
    print("  If no:  setAcquisitionParameters is not taking effect.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

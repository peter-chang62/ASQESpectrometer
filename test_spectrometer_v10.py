"""
Diagnostic test for ASQE Spectrometer — version 10.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v10.py

v9 outcome
----------
  clearMemory (0x07): TIMEOUT — write-only, no response.  This is expected.
  setAcquisitionParameters: byte[1]=0x0D for every payload variant tried (7 total
    across v7–v9: different count widths, scan modes, exposure times, padding).

Conclusion
----------
  byte[1]=0x0D is NOT an error code.  Seven completely different payloads all return
  the identical response — a device that was parsing and rejecting the payload would
  produce different errors.  0x0D (13) is a fixed device constant (firmware version,
  protocol marker, etc.) that always occupies byte[1] of the setAcquisitionParameters
  ACK.  The command is almost certainly succeeding.

  Root cause of v8 setFrameFormat garbage:
    In v8 the pipeline was: setAcqParams → read response → drain(100ms×8) → write
    setFrameFormat → read response → got garbage (0x00 A8 58...).
    Hypothesis: the device sends a SECOND delayed packet after setAcquisitionParameters
    (a "parameters applied" notification).  drain() has a 100ms-per-read timeout —
    if that second packet arrives after drain() finishes, it gets mistakenly read as
    the setFrameFormat response.

This test
---------
  Phase 0  getStatus + getFrameFormat (0x08) baseline — confirm read commands work
           and print current frame format.

  Phase 1  setAcquisitionParameters (0x03) — current layout, scanMode=3,
           exposureTime=5000 (50 ms).  Treat any response as non-fatal.

  Phase 2  Second-packet probe: immediately try to read ONE MORE packet after the
           setAcquisitionParameters ACK (timeout 300 ms).  Print whatever arrives
           (or "none" if timeout).  This directly tests the two-response hypothesis.

  Phase 3  Extra drain + sleep(0.5) to flush any further delayed packets.

  Phase 4  setFrameFormat (0x04) — now the buffer should be clean.

  Phase 5  triggerAcquisition + poll + getFrame.
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
    """Read one packet; strip leading 0x0D report-ID prefix (Windows HID)."""
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
    count = 0
    for _ in range(max_reads):
        r = dev.read(65, timeout_ms)
        if not r:
            break
        count += 1
    return count


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

# ── Phase 0: getStatus + getFrameFormat baseline ───────────────────────────────

print("\n=== Phase 0: getStatus + getFrameFormat baseline ===")

write65(dev, 0x01)
data = read_norm(dev, 500)
if data is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", data[0] == 0x01,
      f"got 0x{data[0]:02X}" if data[0] != 0x01 else "")
print(f"  statusFlags=0x{data[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(data),2)[0]}")
hexdump(data)
drain(dev)

print()
write65(dev, 0x08)   # getFrameFormat
data = read_norm(dev, 500)
if data is None:
    check("getFrameFormat responds", False, "timeout")
else:
    cmd_ok = (data[0] == 0x08)
    check("getFrameFormat cmd echo = 0x08", cmd_ok,
          f"got 0x{data[0]:02X}" if not cmd_ok else "")
    hexdump(data)
    if len(data) >= 8:
        start_el = struct.unpack_from('<H', bytes(data), 1)[0]
        end_el   = struct.unpack_from('<H', bytes(data), 3)[0]
        red_mode = data[5]
        num_pix  = struct.unpack_from('<H', bytes(data), 6)[0]
        print(f"  startElement={start_el}  endElement={end_el}  "
              f"reductionMode={red_mode}  numPixels={num_pix}")
drain(dev)

# ── Phase 1: setAcquisitionParameters ─────────────────────────────────────────

print("\n=== Phase 1: setAcquisitionParameters ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000 (50 ms)")

acq_payload  = list(struct.pack('<H', 1))      # numOfScans=1 (u16)
acq_payload += list(struct.pack('<H', 0))      # numOfBlankScans=0 (u16)
acq_payload += [3]                             # scanMode=3 (u8)
acq_payload += list(struct.pack('<I', 5000))   # exposureTime=5000 (u32)

print(f"  packet bytes: {[f'0x{b:02X}' for b in [0x03] + acq_payload]}")
write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    check("setAcquisitionParameters: first response", False, "timeout")
    sys.exit(1)

print(f"  first response [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
hexdump(data)
check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03,
      f"got 0x{data[0]:02X}" if data[0] != 0x03 else "")
print(f"  byte[1] = 0x{data[1]:02X} ({data[1]})  ← treating as non-fatal")

# ── Phase 2: Second-packet probe ───────────────────────────────────────────────

print("\n=== Phase 2: second-packet probe (300 ms window) ===")
print("  Checking for a delayed second response after setAcquisitionParameters ...")
second = read_norm(dev, 300)
if second is None:
    print("  No second packet arrived within 300 ms — single-response command.")
    print("  (v8's garbage setFrameFormat response had another cause)")
else:
    print(f"  SECOND PACKET ARRIVED: {[f'0x{b:02X}' for b in second[:8]]}")
    hexdump(second)
    print("  → device sends TWO responses to setAcquisitionParameters!")

# ── Phase 3: drain + sleep to flush any further delayed packets ────────────────

print("\n=== Phase 3: drain + sleep to ensure clean buffer ===")
drained = drain(dev, timeout_ms=150, max_reads=6)
print(f"  drained {drained} extra packet(s)")
sleep(0.5)
drained2 = drain(dev, timeout_ms=150, max_reads=6)
print(f"  drained {drained2} more packet(s) after 500 ms sleep")
print("  buffer should now be clean")

# ── Phase 4: setFrameFormat ────────────────────────────────────────────────────

print("\n=== Phase 4: setFrameFormat ===")
sf_payload  = list(struct.pack('<H', 0))     # startElement=0
sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
sf_payload += [0]                            # reductionMode=0

print(f"  packet bytes: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
write65(dev, 0x04, sf_payload)
data = read_norm(dev, 500)
if data is None:
    check("setFrameFormat: response received", False, "timeout")
    print("  *** TIMEOUT — device did not respond ***")
else:
    print(f"  response [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
    hexdump(data)
    cmd_ok = (data[0] == 0x04)
    err_ok = (data[1] == 0)
    check("setFrameFormat cmd echo = 0x04", cmd_ok,
          f"got 0x{data[0]:02X}" if not cmd_ok else "")
    check("setFrameFormat error_code = 0", err_ok,
          f"byte[1] = 0x{data[1]:02X} ({data[1]})" if not err_ok else "")
    if cmd_ok and len(data) >= 4:
        num_pixels = struct.unpack_from('<H', bytes(data), 2)[0]
        print(f"  numPixelsInFrame = {num_pixels}  {'← expect 3648' if num_pixels == 3648 else ''}")
drain(dev)

# ── Phase 5: triggerAcquisition + poll + getFrame ─────────────────────────────

print("\n=== Phase 5: triggerAcquisition + poll ===")
print("  sending triggerAcquisition (0x06)")
write65(dev, 0x06)

frames = 0
last_sd = None
for poll in range(200):
    sleep(0.025)
    write65(dev, 0x01)
    sd = read_norm(dev, 200)
    if sd and sd[0] == 0x01:
        last_sd = sd
        frames = struct.unpack_from('<H', bytes(sd), 2)[0]
        if frames > 0 or poll % 20 == 0:
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}  "
                  f"acq_active={bool(sd[1]&0x01)}")
        if frames > 0:
            break
else:
    print("  framesInMemory never > 0 after 5 s")

check("frame ready within 5 s", frames > 0)

if frames > 0:
    print("\n=== Phase 5b: getFrame(0xFFFF) ===")
    num_pixels = 3648
    packets_needed = (num_pixels + 29) // 30   # = 122

    fp  = list(struct.pack('<H', 0))         # pixelOffset=0
    fp += list(struct.pack('<H', 0xFFFF))    # frameIndex=0xFFFF (averaging mode)
    fp += [packets_needed]

    print(f"  requesting {packets_needed} packets")
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
            if idx >= num_pixels:
                break
            buf[idx] = struct.unpack_from('<H', bytes(pkt), 4 + i * 2)[0]

    if ok:
        check("getFrame: all packets received", True)
        nonzero = sum(1 for v in buf[:num_pixels] if v > 0)
        print(f"  first 8 pixels:   {buf[:8]}")
        print(f"  pixels 1820:1828: {buf[1820:1828]}")
        print(f"  non-zero pixels:  {nonzero} / {num_pixels}")
        check("getFrame: non-zero pixel data", nonzero > 0)
    else:
        check("getFrame: all packets received", False)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
if frames > 0:
    print("\n  *** FRAME CAPTURED — byte[1]=0x0D is non-fatal ***")
    print("  Fix: remove the 'if data[1] != 0: raise' check in")
    print("  libspec.py configure_acquisition() (lines 253 and 260).")
else:
    print("\n  No frame captured.")
    print("  Key findings to check above:")
    print("    Phase 2 — did a second packet arrive after setAcquisitionParameters?")
    print("    Phase 4 — did setFrameFormat get a proper 0x04 echo this time?")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

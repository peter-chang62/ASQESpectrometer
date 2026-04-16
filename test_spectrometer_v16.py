"""
Diagnostic test for ASQE Spectrometer — version 16.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v16.py

v15 outcome
-----------
  getFrame (0x0A) NEVER responds regardless of frameIndex or timing:
    - 200 ms after trigger, frameIndex=0   → timeout on packet 1
    - On byte[4] 0x00→0x01 transition      → timeout on packet 1 (×10)
    - getFrame(0xFFFF)                     → timeout on packet 1
  opcode 0x0A appears to be either unrecognized or blocked entirely.

  getStatus (0x01), setAcquisitionParameters (0x03), getFrameFormat (0x08)
  all respond correctly — so the basic HID write/read works.

Root cause hypothesis
---------------------
  We have never actually tested readFlash (0x1A), which libspec.py uses
  to read calibration data from the device.  If 0x1A responds correctly,
  the protocol is working and 0x0A has a specific issue (wrong opcode or
  needs a prerequisite).  If 0x1A ALSO times out, something is wrong with
  our protocol implementation for multi-packet commands.

  Separately: unknown opcodes 0x02, 0x05, 0x09 may be getAcquisitionParameters
  or an alternative getFrame opcode — worth probing.

This test
---------
  Phase 0  readFlash(offset=0, burst_count=1) — 60 bytes from start of flash.
           Flash starts with ASCII calibration file (model/serial line).
           A readable ASCII response proves multi-packet protocol works.

  Phase 1  readFlash(offset=0, burst_count=5) — 300 bytes.
           Show calibration file header as text.

  Phase 2  Opcode probe — send each of 0x02, 0x05, 0x09 with empty payload
           and a 300 ms read timeout; log any response or timeout.
           Any response = recognized command.
           Response of echo+0x0D+fill = recognized but no useful data.
           Response with non-fill data = potentially useful command.
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


def hexdump(data, indent="    ", max_bytes=64):
    if not data:
        print(f"{indent}(empty)")
        return
    for start in range(0, min(len(data), max_bytes), 16):
        chunk = data[start:start + 16]
        hex_part = " ".join(f"{x:02X}" for x in chunk)
        asc_part = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"{indent}{hex_part:<48}  {asc_part}")


def drain(dev, timeout_ms=150, max_reads=8):
    for _ in range(max_reads):
        if not dev.read(65, timeout_ms):
            break


def is_fill_pattern(data, start=2):
    """Return True if data[start:] is the sequential 0x01 0x02 0x03 ... fill."""
    for i, b in enumerate(data[start:start + 8]):
        if b != (i + 1) & 0xFF:
            return False
    return True


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

print()
sd = read_norm(dev, 0) or []   # non-blocking peek (already drained)
write65(dev, 0x01)
sd = read_norm(dev, 500)
if sd:
    print(f"  getStatus: framesInMemory={struct.unpack_from('<H', bytes(sd), 2)[0]}  "
          f"byte[4]=0x{sd[4]:02X}")
    drain(dev)

# ── Phase 0: readFlash — 1 burst (60 bytes at offset 0) ──────────────────────

print("\n=== Phase 0: readFlash(offset=0, burst_count=1) ===")
print("  Expecting: 60 bytes of ASCII calibration text starting with model/serial")

rf_payload = list(struct.pack('<I', 0)) + [1]   # offset=0, burst_count=1
write65(dev, 0x1A, rf_payload)

pkt0 = read_norm(dev, 1000)
if pkt0 is None:
    print("  TIMEOUT — readFlash (0x1A) did not respond")
    check("readFlash responds (0x1A)", False, "timeout")
else:
    ok_echo = pkt0[0] == 0x1A
    check("readFlash cmd echo = 0x1A", ok_echo,
          f"got 0x{pkt0[0]:02X}" if not ok_echo else "")
    if ok_echo:
        remaining  = pkt0[3]
        loc_offset = struct.unpack_from('<H', bytes(pkt0), 1)[0]
        payload60  = bytes(pkt0[4:64])
        print(f"  remaining={remaining}  local_offset={loc_offset}")
        print(f"  raw hex:")
        hexdump(list(payload60))
        printable = payload60.decode('ascii', errors='replace')
        print(f"  as text:  {repr(printable[:60])}")
        check("readFlash: non-empty response data",
              any(b != 0xFF and b != 0x00 for b in payload60))
drain(dev)

# ── Phase 1: readFlash — 5 bursts (300 bytes at offset 0) ────────────────────

print("\n=== Phase 1: readFlash(offset=0, burst_count=5) ===")
print("  Reading 300 bytes — covers model line + irradiance coef + padding start")

rf_payload5 = list(struct.pack('<I', 0)) + [5]   # offset=0, burst_count=5
write65(dev, 0x1A, rf_payload5)

flash_buf = bytearray()
phase1_ok = True
for burst_n in range(1, 6):
    pkt = read_norm(dev, 1000)
    if pkt is None or pkt[0] != 0x1A:
        got = f"0x{pkt[0]:02X}" if pkt else "timeout"
        print(f"  burst {burst_n}/5: bad reply {got}")
        phase1_ok = False
        break
    remaining = pkt[3]
    expected  = 5 - burst_n
    if remaining != expected:
        print(f"  burst {burst_n}/5: remaining={remaining} expected={expected}")
        phase1_ok = False
        break
    loc_offset = struct.unpack_from('<H', bytes(pkt), 1)[0]
    chunk = bytes(pkt[4:64])
    flash_buf.extend(chunk)

if phase1_ok and flash_buf:
    check("readFlash: all 5 bursts received", True)
    text_data = flash_buf.decode('ascii', errors='replace')
    lines = text_data.splitlines()
    print(f"  {len(flash_buf)} bytes, {len(lines)} lines:")
    for i, ln in enumerate(lines[:10]):
        print(f"    line {i+1:2d}: {repr(ln)}")
    if len(lines) > 10:
        print(f"    ... ({len(lines)} total lines in 300 bytes)")
elif not phase1_ok:
    check("readFlash: all 5 bursts received", False)
drain(dev)

# ── Phase 2: opcode probe ─────────────────────────────────────────────────────

print("\n=== Phase 2: opcode probe ===")
print("  Probing unknown opcodes to find getAcquisitionParameters / alt getFrame")
print("  Response pattern: echo + 0x0D + fill = recognized but no data")
print("  Response pattern: echo + other data  = potentially useful")

PROBE_OPCODES = [
    (0x02, "unknown — possibly getAcquisitionParameters?"),
    (0x05, "unknown — gap between setFrameFormat and triggerAcquisition"),
    (0x09, "unknown — gap between getFrameFormat and getFrame"),
    (0x0B, "unknown — after getFrame opcode"),
]

for opcode, desc in PROBE_OPCODES:
    write65(dev, opcode)
    resp = read_norm(dev, 300)
    if resp is None:
        print(f"  0x{opcode:02X} ({desc}): NO RESPONSE (write-only or unrecognized)")
    else:
        fill = is_fill_pattern(resp, start=2)
        tag = "fill pattern (recognized, no data)" if fill else "NON-FILL DATA — inspect!"
        print(f"  0x{opcode:02X} ({desc}):")
        print(f"    echo=0x{resp[0]:02X}  byte[1]=0x{resp[1]:02X}  {tag}")
        hexdump(resp[:16], indent="    ")
    drain(dev)
    sleep(0.05)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
flash_ok = any("readFlash" in lbl and ok for lbl, ok in _results)
if flash_ok:
    print("  readFlash (0x1A) WORKS — multi-packet protocol is correct.")
    print("  getFrame (0x0A) issue is specific to frame acquisition state.")
    print("  Next step: determine why the device never stores frames.")
    print("  Check opcode probe above for unrecognized/alternative commands.")
else:
    print("  readFlash (0x1A) FAILED — protocol issue for multi-packet commands.")
    print("  Investigate HID packet format, report ID handling, or opcode mapping.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

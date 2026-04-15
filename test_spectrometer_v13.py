"""
Diagnostic test for ASQE Spectrometer — version 13.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v13.py

v12 outcome
-----------
  setAcquisitionParameters registers (bytes[4,6] change in getStatus).
  setFrameFormat sent write-only — 1 packet drained afterward (first bytes:
  00 00 A8 48 ...; starts with 0x00, not a normal Windows response).
  triggerAcquisition sent — acq_active NEVER went True, framesInMemory
  stayed 0 for the full 5-second poll.

Root cause hypothesis
---------------------
  libspec.py's clear_memory() (0x07) expects a response and is a separate
  step from configure_acquisition().  capture_frame() does NOT call
  clearMemory before triggering — but the device may require it to put
  the frame buffer into a ready state, even when framesInMemory=0.

  Secondary: libspec.py's configure_acquisition() would raise an error
  because it checks data[1] != 0 after setAcquisitionParameters, and the
  device always returns byte[1]=0x0D (non-zero).  The parameters ARE
  registering (status bytes change), so 0x0D is a non-error field.
  This is a libspec.py bug to fix separately.

This test
---------
  Phase 0  getStatus baseline (full 16-byte hex dump).
  Phase 1  setAcquisitionParameters: numOfScans=1, scanMode=0,
           exposureTime=5000 (50 ms). Log byte[1].
  Phase 2  getStatus — verify bytes[4,6] changed.
  Phase 3  setFrameFormat (0x04) write-only; sleep 200 ms; drain with
           FULL packet logging (all bytes, not just first 8).
  Phase 4  [NEW] clearMemory (0x07) — read response, check echo and data[1].
  Phase 5  getStatus — check state immediately before trigger.
  Phase 6  triggerAcquisition (0x06) write-only; probe for response (50 ms).
  Phase 7  Poll getStatus up to 5 s for framesInMemory > 0.
  Phase 8  getFrame(frameIndex=0) if frames > 0.
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


def drain(dev, timeout_ms=150, max_reads=6, label=""):
    count = 0
    for _ in range(max_reads):
        r = dev.read(65, timeout_ms)
        if not r:
            break
        count += 1
        tag = f"  [{label}] " if label else "  "
        raw = list(r)
        print(f"{tag}drained packet ({len(raw)} bytes):")
        hexdump(raw, indent=tag + "  ")
    if count == 0 and label:
        print(f"  [{label}] nothing to drain")
    return count


def get_status(dev):
    write65(dev, 0x01)
    return read_norm(dev, 500)


def print_status(sd, indent="  "):
    if sd is None:
        print(f"{indent}(timeout)")
        return
    flags = sd[1]
    frames = struct.unpack_from('<H', bytes(sd), 2)[0]
    acq = bool(flags & 0x01)
    mem_full = bool(flags & 0x02)
    print(f"{indent}statusFlags=0x{flags:02X}  acq_active={acq}  "
          f"mem_full={mem_full}  framesInMemory={frames}")
    hexdump(sd, indent=indent)


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
drain(dev, label="startup")

# ── Phase 0: getStatus baseline ───────────────────────────────────────────────

print("\n=== Phase 0: getStatus baseline ===")
status0 = get_status(dev)
if status0 is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", status0[0] == 0x01)
print_status(status0)
drain(dev)

# ── Phase 1: setAcquisitionParameters ─────────────────────────────────────────

print("\n=== Phase 1: setAcquisitionParameters ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=0, exposureTime=5000 (50 ms)")

acq_payload  = list(struct.pack('<H', 1))      # numOfScans=1
acq_payload += list(struct.pack('<H', 0))      # numOfBlankScans=0
acq_payload += [0]                             # scanMode=0
acq_payload += list(struct.pack('<I', 5000))   # exposureTime=5000 (50 ms)

write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    check("setAcquisitionParameters responds", False, "timeout")
else:
    check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03,
          f"got 0x{data[0]:02X}" if data[0] != 0x03 else "")
    print(f"  byte[1]=0x{data[1]:02X}  "
          f"({'0x0D = expected non-fatal field' if data[1] == 0x0D else 'unexpected value'})")
drain(dev)

# ── Phase 2: getStatus post-setAcquisitionParameters ─────────────────────────

print("\n=== Phase 2: getStatus (post-setAcquisitionParameters) ===")
status2 = get_status(dev)
if status2:
    print_status(status2)
    changed = [(i, status0[i], status2[i])
               for i in range(min(len(status0), len(status2)))
               if status0[i] != status2[i]]
    if changed:
        print("  CHANGED bytes vs baseline: " +
              ", ".join(f"[{i}]: 0x{a:02X}→0x{b:02X}" for i, a, b in changed))
    else:
        print("  WARNING: no bytes changed — setAcquisitionParameters may not have taken effect")
drain(dev)

# ── Phase 3: setFrameFormat — write-only (full drain logging) ─────────────────

print("\n=== Phase 3: setFrameFormat (write-only) + drain ===")
sf_payload  = list(struct.pack('<H', 0))     # startElement=0
sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
sf_payload += [0]                            # reductionMode=0
print(f"  sending 0x04: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
write65(dev, 0x04, sf_payload)
sleep(0.2)
drained = drain(dev, label="post-setFrameFormat")
print(f"  drained {drained} packet(s) total")

# ── Phase 4: clearMemory ──────────────────────────────────────────────────────

print("\n=== Phase 4: clearMemory (0x07) ===")
write65(dev, 0x07)
cm_data = read_norm(dev, 500)
if cm_data is None:
    print("  TIMEOUT — no response (clearMemory may be write-only on this firmware)")
    check("clearMemory responded", False, "timeout — proceeding anyway")
else:
    ok_echo = cm_data[0] == 0x07
    check("clearMemory cmd echo = 0x07", ok_echo,
          f"got 0x{cm_data[0]:02X}" if not ok_echo else "")
    print(f"  data[1]=0x{cm_data[1]:02X}  "
          f"({'success' if cm_data[1] == 0x00 else f'non-zero — possible error'})")
    hexdump(cm_data)
drain(dev)

# ── Phase 5: getStatus pre-trigger ────────────────────────────────────────────

print("\n=== Phase 5: getStatus (pre-trigger) ===")
status5 = get_status(dev)
if status5:
    print_status(status5)
drain(dev)

# ── Phase 6: triggerAcquisition + response probe ──────────────────────────────

print("\n=== Phase 6: triggerAcquisition (0x06) ===")
print("  sending triggerAcquisition")
write65(dev, 0x06)
probe = dev.read(65, 50)   # probe for response — do NOT use read_norm (avoids queuing issues)
if probe:
    print(f"  *** triggerAcquisition DID produce a response ({len(probe)} bytes):")
    hexdump(list(probe))
else:
    print("  no response within 50 ms — write-only confirmed")

# ── Phase 7: poll for frames ──────────────────────────────────────────────────

print("\n=== Phase 7: triggerAcquisition + poll ===")
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
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): acq_active went TRUE")
            acq_was_active = True
        if not acq_active and acq_was_active:
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): acq_active went FALSE")
            acq_was_active = False
        if frames > 0 or poll % 20 == 0:
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}  "
                  f"acq_active={acq_active}")
        if frames > 0:
            break
else:
    print("  framesInMemory never > 0 after 5 s")
    if last_sd:
        print("  final status:")
        print_status(last_sd, indent="    ")

check("frame ready within 5 s", frames > 0)

# ── Phase 8: getFrame(0) ──────────────────────────────────────────────────────

if frames > 0:
    print("\n=== Phase 8: getFrame(frameIndex=0) ===")
    NUM_PIXELS = 3648
    packets_needed = (NUM_PIXELS + 29) // 30   # = 122

    fp  = list(struct.pack('<H', 0))            # pixelOffset=0
    fp += list(struct.pack('<H', 0))            # frameIndex=0
    fp += [packets_needed]

    print(f"  requesting {packets_needed} packets for {NUM_PIXELS} pixels")
    write65(dev, 0x0A, fp)

    buf = [0] * NUM_PIXELS
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
        nonzero = sum(1 for v in buf if v > 0)
        print(f"  first 8 pixels:   {buf[:8]}")
        print(f"  pixels 1820-1828: {buf[1820:1828]}")
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
    print("  Next step: fix libspec.py configure_acquisition():")
    print("    1. Change setFrameFormat to _write() (no response expected)")
    print("    2. Call _get_frame_format() after setFrameFormat to get pixel count")
    print("    3. Remove data[1] != 0 check from setAcquisitionParameters")
    print("    4. Add clear_memory() call before capture_frame()")
else:
    print("  No frame captured.")
    print("  Check Phase 4 (clearMemory response) — if timeout, device may need")
    print("  a different pre-trigger sequence.")
    print("  Check Phase 6 (trigger response probe) — if a packet appeared,")
    print("  triggerAcquisition is NOT write-only and the queue is getting confused.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

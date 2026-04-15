"""
Diagnostic test for ASQE Spectrometer — version 15.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v15.py

v14 outcome
-----------
  getFrameFormat (0x08) response is all fill-pattern — no meaningful data
  on this firmware.  The device returns byte[1]=0x0D then a sequential
  fill counter (0x01 0x02 0x03 ...) filling the rest of the packet.
  The pixel count libspec.py tries to read at bytes[6:8] is garbage.

  byte[4] of getStatus oscillates ~every 30-100 ms after triggerAcquisition
  — matching our 30 ms exposure time.  The CCD IS scanning after trigger.

  framesInMemory (bytes[2:4]) stays 0 throughout despite the CCD scanning.
  getFrame(0xFFFF) timed out in the blind probe.

Root cause hypotheses
---------------------
  1. framesInMemory never increments in scanMode=0/1 because these modes
     keep a rolling live buffer rather than a stored-frame model.  The
     device has no "stored frames" — just a current scan result.  Calling
     getFrame without waiting for framesInMemory may work.

  2. getFrame(0xFFFF) is only valid for scanMode=3 (Frame Averaging).
     For scanMode=0 the correct frameIndex is 0, not 0xFFFF.

This test
---------
  Phase 0  setAcquisitionParameters: scanMode=0, numOfScans=1,
           exposureTime=1000 (10 ms).  Skip setFrameFormat.

  Phase 1  triggerAcquisition.

  Phase 2  Wait 200 ms (plenty of time for multiple 10 ms scans), then
           call getFrame(frameIndex=0) with 122 packets immediately —
           NO framesInMemory gate.  Log outcome.

  Phase 3  If Phase 2 failed: watch byte[4] transitions (0x00→0x01 =
           scan complete); call getFrame(frameIndex=0) as soon as the
           transition is detected.  Timeout 5 s.

  Phase 4  If both above failed: re-trigger and try getFrame(0xFFFF)
           in case scanMode=0 uses latest-frame semantics.
"""

import sys
import struct
from time import sleep, time

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
    for start in range(0, min(len(data), max_bytes), 16):
        chunk = data[start:start + 16]
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


def get_frame(dev, frame_index, num_pixels=3648):
    """Send getFrame request and read all packets.
    Returns (buf, ok) where buf is the pixel list and ok is True on success.
    """
    packets_needed = (num_pixels + 29) // 30  # ceil(3648/30) = 122
    fp  = list(struct.pack('<H', 0))                 # pixelOffset=0
    fp += list(struct.pack('<H', frame_index))        # frameIndex
    fp += [packets_needed]
    write65(dev, 0x0A, fp)

    buf = [0] * num_pixels
    for n in range(1, packets_needed + 1):
        pkt = read_norm(dev, 300)
        if pkt is None:
            print(f"    packet {n}/{packets_needed}: TIMEOUT")
            return buf, False
        if pkt[0] != 0x0A:
            print(f"    packet {n}/{packets_needed}: wrong echo 0x{pkt[0]:02X}")
            return buf, False
        remaining = pkt[3]
        expected  = packets_needed - n
        if remaining != expected:
            print(f"    packet {n}/{packets_needed}: remaining={remaining} "
                  f"expected={expected}")
            return buf, False
        pixel_offset = struct.unpack_from('<H', bytes(pkt), 1)[0]
        for i in range(30):
            idx = pixel_offset + i
            if idx >= num_pixels:
                break
            buf[idx] = struct.unpack_from('<H', bytes(pkt), 4 + i * 2)[0]
    return buf, True


def report_frame(buf, num_pixels=3648):
    nonzero = sum(1 for v in buf[:num_pixels] if v > 0)
    print(f"  first 8 pixels:   {buf[:8]}")
    print(f"  pixels 1820-1828: {buf[1820:1828]}")
    print(f"  non-zero pixels:  {nonzero} / {num_pixels}")
    return nonzero


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

# ── Baseline ──────────────────────────────────────────────────────────────────

print("\n=== Baseline: getStatus ===")
sd0 = get_status(dev)
if sd0 is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", sd0[0] == 0x01)
b4 = sd0[4] if len(sd0) > 4 else 0
frames = struct.unpack_from('<H', bytes(sd0), 2)[0]
print(f"  statusFlags=0x{sd0[1]:02X}  framesInMemory={frames}  byte[4]=0x{b4:02X}")
drain(dev)

# ── Phase 0: setAcquisitionParameters ────────────────────────────────────────

print("\n=== Phase 0: setAcquisitionParameters ===")
EXPOSURE = 1000   # 10 ms — fast scans
print(f"  scanMode=0  numOfScans=1  exposureTime={EXPOSURE} (10 ms)  [NO setFrameFormat]")

acq_payload  = list(struct.pack('<H', 1))           # numOfScans=1
acq_payload += list(struct.pack('<H', 0))           # numOfBlankScans=0
acq_payload += [0]                                  # scanMode=0
acq_payload += list(struct.pack('<I', EXPOSURE))    # exposureTime

write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    check("setAcquisitionParameters responds", False, "timeout")
else:
    check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03)
    print(f"  byte[1]=0x{data[1]:02X}")
drain(dev)

# ── Phase 1: triggerAcquisition ───────────────────────────────────────────────

print("\n=== Phase 1: triggerAcquisition ===")
print("  sending trigger (0x06)")
write65(dev, 0x06)

# ── Phase 2: direct getFrame(0) after 200 ms ─────────────────────────────────

print("\n=== Phase 2: wait 200 ms then getFrame(frameIndex=0) directly ===")
print("  (no framesInMemory check — testing live-buffer hypothesis)")
sleep(0.2)

buf2, ok2 = get_frame(dev, frame_index=0)
if ok2:
    check("Phase 2: getFrame(0) succeeded", True)
    nz = report_frame(buf2)
    check("Phase 2: non-zero pixel data", nz > 0)
else:
    check("Phase 2: getFrame(0) succeeded", False)
    drain(dev)

# ── Phase 3: poll for byte[4] transition, then getFrame(0) ───────────────────

if not ok2:
    print("\n=== Phase 3: watch byte[4] for 0x00→0x01 transition → getFrame(0) ===")
    print("  polling every 10 ms; call getFrame(0) on first 0x00→0x01 transition")
    prev_b4 = b4
    deadline = time() + 5.0
    phase3_ok = False

    # Re-trigger first
    write65(dev, 0x06)
    print("  re-triggered")

    while time() < deadline:
        sleep(0.010)
        sd = get_status(dev)
        if sd is None or sd[0] != 0x01:
            continue
        cur_b4 = sd[4] if len(sd) > 4 else 0

        if prev_b4 == 0x00 and cur_b4 == 0x01:
            elapsed = (5.0 - (deadline - time())) * 1000
            print(f"  {elapsed:.0f} ms: byte[4] 0x00→0x01 detected! calling getFrame(0) now")
            buf3, ok3 = get_frame(dev, frame_index=0)
            if ok3:
                check("Phase 3: getFrame(0) on byte[4] transition", True)
                nz = report_frame(buf3)
                check("Phase 3: non-zero pixel data", nz > 0)
                phase3_ok = True
                break
            else:
                print("  getFrame(0) failed after transition — continuing poll")
                drain(dev)
                write65(dev, 0x06)  # re-trigger for next try
        prev_b4 = cur_b4

    if not phase3_ok:
        check("Phase 3: getFrame(0) on byte[4] transition", False,
              "no successful read within 5 s")

        # ── Phase 4: re-trigger, try getFrame(0xFFFF) ─────────────────────────

        print("\n=== Phase 4: re-trigger + getFrame(0xFFFF) ===")
        print("  (0xFFFF = 'latest frame' semantics)")
        write65(dev, 0x06)
        sleep(0.2)

        buf4, ok4 = get_frame(dev, frame_index=0xFFFF)
        if ok4:
            check("Phase 4: getFrame(0xFFFF) succeeded", True)
            nz = report_frame(buf4)
            check("Phase 4: non-zero pixel data", nz > 0)
        else:
            check("Phase 4: getFrame(0xFFFF) succeeded", False)
            drain(dev)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
succeeded = any(ok for _, ok in _results if "getFrame" in _)
if any("non-zero pixel data" in lbl and ok for lbl, ok in _results):
    print("  *** FRAME DATA RECEIVED ***")
    print("  Next steps:")
    print("    1. Fix libspec.py: call getFrame directly after trigger,")
    print("       no framesInMemory gating needed.")
    print("    2. getFrameFormat fill pattern = no pixel count from device;")
    print("       hardcode _num_pixels_in_frame = 3648.")
    print("    3. Remove data[1]!=0 check from setAcquisitionParameters.")
    print("    4. setFrameFormat: use _write() not _write_read().")
else:
    print("  No frame data received.")
    print("  Phase 2 result tells us whether getFrame works without polling.")
    print("  Phase 3 result tells us whether byte[4] is a frame-ready signal.")
    print("  Phase 4 result tells us whether 0xFFFF indexing works for scanMode=0.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

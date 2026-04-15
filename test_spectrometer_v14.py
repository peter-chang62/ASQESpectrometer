"""
Diagnostic test for ASQE Spectrometer — version 14.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v14.py

v13 outcome
-----------
  clearMemory (0x07) is write-only on this firmware — no response.
  triggerAcquisition (0x06) is write-only — confirmed.
  setFrameFormat (0x04) drains a 64-byte artifact packet (garbage data,
    Windows HIDAPI buffer noise — not a device response).
  After triggerAcquisition byte[4] of getStatus changes 0x01→0x00,
  confirming the trigger was accepted. But framesInMemory (bytes[2:4])
  stays 0 for the full 5-second poll.

Root cause hypothesis
---------------------
  scanMode=0 (Continuous): CCD reads continuously; trigger stores
  num_of_scans frames.  If the CCD has not yet completed even one scan
  at the moment the trigger fires, the device accepts the trigger but
  never stores a frame because the pipeline isn't producing data.

  scanMode=1 (First Frame Idle): CCD is IDLE until trigger; trigger
  kicks off numOfScans scans from scratch and stores them.  This
  eliminates the "CCD not yet running" race condition entirely.

  Secondary probe: if framesInMemory is stuck at 0 due to a firmware
  quirk, a blind getFrame(0xFFFF) call will reveal whether data
  actually exists in device RAM.

This test
---------
  Phase 0  getStatus baseline — show bytes[0:8] plus fill pattern.
  Phase 1  setAcquisitionParameters: numOfScans=1, scanMode=1,
           exposureTime=3000 (30 ms).
  Phase 2  getStatus — verify bytes changed (params registered).
  Phase 3  setFrameFormat (0x04) write-only; drain.
  Phase 4  getFrameFormat (0x08) — read back pixel count to confirm
           device accepted the frame range.
  Phase 5  triggerAcquisition (0x06); note byte[4] transition.
  Phase 6  Poll getStatus up to 5 s; track framesInMemory AND byte[4].
  Phase 7  getFrame(0xFFFF) if framesInMemory > 0.
  Phase 7b Blind getFrame probe if framesInMemory stayed 0:
           request 1 packet and log raw reply — tests whether frames
           exist but framesInMemory is not being updated.
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
    if label and count == 0:
        print(f"  [{label}] nothing to drain")
    return count


def get_status(dev):
    write65(dev, 0x01)
    return read_norm(dev, 500)


def print_status(sd, indent="  ", label=""):
    if sd is None:
        print(f"{indent}(timeout)")
        return
    flags = sd[1]
    frames = struct.unpack_from('<H', bytes(sd), 2)[0]
    b4 = sd[4] if len(sd) > 4 else "?"
    acq = bool(flags & 0x01)
    mem_full = bool(flags & 0x02)
    prefix = f"[{label}] " if label else ""
    print(f"{indent}{prefix}statusFlags=0x{flags:02X}  acq_active={acq}  "
          f"mem_full={mem_full}  framesInMemory={frames}  byte[4]=0x{b4:02X}")
    hexdump(sd[:8], indent=indent + "  ")


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
SCAN_MODE = 1        # First Frame Idle — CCD idle until trigger
EXPOSURE  = 3000     # 30 ms
print(f"  numOfScans=1, numOfBlankScans=0, scanMode={SCAN_MODE}, "
      f"exposureTime={EXPOSURE} ({EXPOSURE//100} ms)")

acq_payload  = list(struct.pack('<H', 1))           # numOfScans=1
acq_payload += list(struct.pack('<H', 0))           # numOfBlankScans=0
acq_payload += [SCAN_MODE]                          # scanMode
acq_payload += list(struct.pack('<I', EXPOSURE))    # exposureTime

write65(dev, 0x03, acq_payload)
data = read_norm(dev, 500)
if data is None:
    check("setAcquisitionParameters responds", False, "timeout")
else:
    check("setAcquisitionParameters cmd echo = 0x03", data[0] == 0x03,
          f"got 0x{data[0]:02X}" if data[0] != 0x03 else "")
    print(f"  byte[1]=0x{data[1]:02X}  "
          f"({'0x0D = expected non-fatal field' if data[1] == 0x0D else 'unexpected'})")
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
        print("  WARNING: no bytes changed — params may not have registered")
drain(dev)

# ── Phase 3: setFrameFormat — write-only ─────────────────────────────────────

print("\n=== Phase 3: setFrameFormat (write-only) + drain ===")
sf_payload  = list(struct.pack('<H', 0))     # startElement=0
sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
sf_payload += [0]                            # reductionMode=0
print(f"  sending 0x04: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
write65(dev, 0x04, sf_payload)
sleep(0.2)
drain(dev, label="post-setFrameFormat")

# ── Phase 4: getFrameFormat (0x08) ────────────────────────────────────────────

print("\n=== Phase 4: getFrameFormat (0x08) ===")
write65(dev, 0x08)
ff_data = read_norm(dev, 500)
if ff_data is None:
    print("  TIMEOUT — no response")
    check("getFrameFormat responds", False, "timeout")
    num_pixels = 3648   # fallback
else:
    ok_echo = ff_data[0] == 0x08
    check("getFrameFormat cmd echo = 0x08", ok_echo,
          f"got 0x{ff_data[0]:02X}" if not ok_echo else "")
    print(f"  raw bytes[0:12]: {[f'0x{b:02X}' for b in ff_data[:12]]}")
    # libspec.py reads _num_pixels_in_frame from bytes[6:8] of response
    num_pixels = struct.unpack_from('<H', bytes(ff_data), 6)[0]
    print(f"  num_pixels (bytes[6:8]): {num_pixels}")
    # Also try bytes[2:4] as alternative location
    alt_pixels = struct.unpack_from('<H', bytes(ff_data), 2)[0]
    print(f"  alt_pixels  (bytes[2:4]): {alt_pixels}")
    hexdump(ff_data[:16])
drain(dev)

# ── Phase 5: triggerAcquisition ───────────────────────────────────────────────

print("\n=== Phase 5: triggerAcquisition (0x06) ===")
status_pre = get_status(dev)
b4_pre = status_pre[4] if status_pre and len(status_pre) > 4 else "?"
print(f"  byte[4] before trigger: 0x{b4_pre:02X}")
drain(dev)

print("  sending triggerAcquisition")
write65(dev, 0x06)

# Immediate probe
probe = dev.read(65, 50)
if probe:
    print(f"  *** trigger produced a response ({len(probe)} bytes):")
    hexdump(list(probe))
else:
    print("  no response within 50 ms — write-only confirmed")

# ── Phase 6: poll for frames ──────────────────────────────────────────────────

print("\n=== Phase 6: poll (5 s) ===")
frames = 0
last_sd = None
prev_b4 = b4_pre
for poll in range(200):
    sleep(0.025)
    sd = get_status(dev)
    if sd and sd[0] == 0x01:
        last_sd = sd
        frames = struct.unpack_from('<H', bytes(sd), 2)[0]
        acq_active = bool(sd[1] & 0x01)
        b4 = sd[4] if len(sd) > 4 else 0

        # Print on significant events or every 20 polls
        b4_changed = (b4 != prev_b4)
        if frames > 0 or b4_changed or poll % 20 == 0:
            change = f"  byte[4]: 0x{prev_b4:02X}→0x{b4:02X}" if b4_changed else ""
            print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                  f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}  "
                  f"acq_active={acq_active}  byte[4]=0x{b4:02X}{change}")
        prev_b4 = b4

        if frames > 0:
            break
else:
    print("  framesInMemory never > 0 after 5 s")
    if last_sd:
        print_status(last_sd, indent="  ", label="final")

check("frame ready within 5 s", frames > 0)

# ── Phase 7: getFrame(0xFFFF) if frames > 0 ──────────────────────────────────

NUM_PIXELS = num_pixels if 0 < num_pixels <= 3648 else 3648
packets_needed = (NUM_PIXELS + 29) // 30

if frames > 0:
    print(f"\n=== Phase 7: getFrame(0xFFFF) — {NUM_PIXELS} pixels / {packets_needed} packets ===")

    fp  = list(struct.pack('<H', 0))       # pixelOffset=0
    fp += list(struct.pack('<H', 0xFFFF))  # frameIndex=latest
    fp += [packets_needed]

    write65(dev, 0x0A, fp)

    buf = [0] * NUM_PIXELS
    ok = True
    for n in range(1, packets_needed + 1):
        pkt = read_norm(dev, 300)
        if pkt is None or pkt[0] != 0x0A:
            got = f"0x{pkt[0]:02X}" if pkt else "timeout"
            print(f"  packet {n}: bad reply {got}")
            ok = False; break
        remaining = pkt[3]
        expected  = packets_needed - n
        if remaining != expected:
            print(f"  packet {n}: remaining={remaining} expected={expected}")
            ok = False; break
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

# ── Phase 7b: blind getFrame probe (framesInMemory == 0) ─────────────────────

else:
    print("\n=== Phase 7b: blind getFrame(0xFFFF) probe ===")
    print("  Requesting 1 packet (frameIndex=0xFFFF) despite framesInMemory=0")
    print("  to test whether frames exist but framesInMemory is not updating.")

    fp  = list(struct.pack('<H', 0))       # pixelOffset=0
    fp += list(struct.pack('<H', 0xFFFF))  # frameIndex=latest
    fp += [1]                              # ask for just 1 packet

    write65(dev, 0x0A, fp)
    probe_pkt = read_norm(dev, 500)

    if probe_pkt is None:
        print("  TIMEOUT — device returned nothing")
        check("blind getFrame: any response", False, "timeout")
    else:
        print(f"  response ({len(probe_pkt)} bytes), byte[0]=0x{probe_pkt[0]:02X}:")
        hexdump(probe_pkt[:16])
        is_frame = probe_pkt[0] == 0x0A
        check("blind getFrame: valid 0x0A echo", is_frame,
              f"got 0x{probe_pkt[0]:02X}" if not is_frame else "")
        if is_frame:
            pixel_offset = struct.unpack_from('<H', bytes(probe_pkt), 1)[0]
            remaining = probe_pkt[3]
            pixels = [struct.unpack_from('<H', bytes(probe_pkt), 4 + i * 2)[0]
                      for i in range(8)]
            print(f"  pixel_offset={pixel_offset}  remaining_packets={remaining}")
            print(f"  first 8 pixels: {pixels}")
            nonzero8 = sum(1 for v in pixels if v > 0)
            check("blind getFrame: non-zero pixel data", nonzero8 > 0,
                  f"{nonzero8}/8 non-zero")
    drain(dev, label="post-blind-getFrame")

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
if frames > 0:
    print("  *** FRAME CAPTURED ***")
    print("  Next: fix libspec.py configure_acquisition():")
    print("    1. Change setFrameFormat to _write() (no response)")
    print("    2. Use getFrameFormat (0x08) for pixel count (bytes[6:8])")
    print("    3. Remove data[1] != 0 check on setAcquisitionParameters")
    print("    4. clearMemory is write-only — remove _write_read in clear_memory()")
else:
    print("  No frame captured.")
    print("  Check Phase 7b: if blind getFrame returned 0x0A with pixel data,")
    print("    framesInMemory is misread — investigate status byte layout.")
    print("  If Phase 7b timed out: device is not acquiring. Consider")
    print("    resetDevice (0xF1) + reconnect as next step.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

"""
Diagnostic test for ASQE Spectrometer — version 9.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v9.py

v8 outcome
----------
  setAcquisitionParameters: byte[1]=0x0D consistently; v8 proceeded past it.
  setFrameFormat: response was garbage (byte[0]=0x00) — device likely did not reply,
    meaning setAcquisitionParameters failure blocks subsequent commands.
  No frames captured.

Root cause hypothesis
---------------------
  The original libspec.py was a ctypes wrapper. Its setAcquisitionParameters signature:

    setAcquisitionParameters(c_uint16, c_uint16, c_uint8, c_uint32)
      = (numOfScans, numOfBlankScans, scanMode, exposureTime)

  With MSVC default struct alignment, a struct of {u16, u16, u8, u32} has 3 bytes of
  padding between the u8 and u32 (to align u32 to a 4-byte boundary):

    Packed (9 bytes, what we've been sending):
      [ns_lo, ns_hi, nbs_lo, nbs_hi, sm, et_0, et_1, et_2, et_3]

    MSVC-aligned (12 bytes, never tried):
      [ns_lo, ns_hi, nbs_lo, nbs_hi, sm, 0x00, 0x00, 0x00, et_0, et_1, et_2, et_3]
                                         ^^^^^^^^^^^^^^^^^^^ 3 padding bytes!

  None of the v7/v8 variants included this padding.  This test probes it.

This test
---------
  Phase 0  getStatus baseline.

  Phase 1  clearMemory (0x07) — no-parameter write command.
           If this also returns byte[1]!=0, ALL write commands are broken.
           If it returns byte[1]=0, the failure is specific to the payload format.

  Phase 2  setAcquisitionParameters with MSVC-aligned 12-byte payload (padded).
           numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000 (50 ms).
           Stops if byte[1]==0 (success) or continues for Phase 3 fallback.

  Phase 3  Fallback: packed 9-byte payload with scanMode=0 and factory-default
           exposureTime=10 (100 µs) — closest to device power-on state.

  Phase 4  setFrameFormat + triggerAcquisition + frame poll (if Phase 2 or 3 succeeded).
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
    pkt = [0x00, opcode] + (payload or [])
    pkt += [0x00] * (65 - len(pkt))
    return dev.write(pkt)


def read_norm(dev, timeout_ms=500):
    """Read one packet; strip leading 0x0D report-ID prefix (Windows)."""
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


def send_and_print(dev, opcode, payload, label, timeout_ms=500):
    """Send a command and print the raw response. Returns (data, byte1)."""
    drain(dev)
    full = [opcode] + payload
    print(f"  packet bytes: {[f'0x{b:02X}' for b in full]}")
    write65(dev, opcode, payload)
    data = read_norm(dev, timeout_ms)
    if data is None:
        print(f"  response: TIMEOUT")
        check(f"{label}: response received", False, "timeout")
        return None, None
    print(f"  raw response [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
    hexdump(data)
    b1 = data[1] if len(data) > 1 else None
    return data, b1


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
data = read_norm(dev, 500)
if data is None:
    check("getStatus responds", False, "timeout"); sys.exit(1)
check("getStatus cmd echo = 0x01", data[0] == 0x01,
      f"got 0x{data[0]:02X}" if data[0] != 0x01 else "")
print(f"  statusFlags=0x{data[1]:02X}  framesInMemory={struct.unpack_from('<H',bytes(data),2)[0]}")
hexdump(data)
drain(dev)

# ── Phase 1: clearMemory — baseline write command (no payload) ────────────────

print("\n=== Phase 1: clearMemory (0x07) — baseline write command ===")
data, b1 = send_and_print(dev, 0x07, [], "clearMemory")
if data is not None:
    cmd_ok = (data[0] == 0x07)
    check("clearMemory cmd echo = 0x07", cmd_ok, f"got 0x{data[0]:02X}" if not cmd_ok else "")
    print(f"  byte[1] = 0x{b1:02X} ({b1})  {'← 0 = success' if b1 == 0 else '← non-zero'}")
drain(dev)

# ── Phase 2: setAcquisitionParameters — MSVC-padded (12-byte payload) ─────────

print("\n=== Phase 2: setAcquisitionParameters — MSVC-aligned 12-byte payload ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=3, exposureTime=5000")
print("  Layout: [ns:u16, nbs:u16, sm:u8, PAD:3, et:u32]  (MSVC default alignment)")

padded_payload  = list(struct.pack('<H', 1))      # numOfScans=1 (u16)
padded_payload += list(struct.pack('<H', 0))      # numOfBlankScans=0 (u16)
padded_payload += [3]                             # scanMode=3 (u8)
padded_payload += [0x00, 0x00, 0x00]             # 3 padding bytes
padded_payload += list(struct.pack('<I', 5000))   # exposureTime=5000 (u32)

data2, b1_p2 = send_and_print(dev, 0x03, padded_payload, "setAcquisitionParameters (padded)")
p2_ok = False
if data2 is not None:
    cmd_ok = (data2[0] == 0x03)
    check("setAcquisitionParameters (padded) cmd echo = 0x03",
          cmd_ok, f"got 0x{data2[0]:02X}" if not cmd_ok else "")
    err_ok = (b1_p2 == 0)
    check("setAcquisitionParameters (padded) error_code = 0", err_ok,
          f"byte[1] = 0x{b1_p2:02X} ({b1_p2})" if not err_ok else "")
    p2_ok = cmd_ok and err_ok
    if p2_ok:
        print("  *** PADDED LAYOUT SUCCEEDED ***")
drain(dev)

# ── Phase 3: Fallback — packed payload, scanMode=0, factory exposureTime=10 ───

print("\n=== Phase 3: setAcquisitionParameters — packed 9-byte, scanMode=0, exposureTime=10 ===")
print("  numOfScans=1, numOfBlankScans=0, scanMode=0, exposureTime=10 (factory default)")
print("  Layout: [ns:u16, nbs:u16, sm:u8, et:u32]  (packed, no padding)")

packed_payload  = list(struct.pack('<H', 1))    # numOfScans=1 (u16)
packed_payload += list(struct.pack('<H', 0))    # numOfBlankScans=0 (u16)
packed_payload += [0]                           # scanMode=0 (u8)
packed_payload += list(struct.pack('<I', 10))   # exposureTime=10 (u32, factory default)

data3, b1_p3 = send_and_print(dev, 0x03, packed_payload, "setAcquisitionParameters (packed, sm=0)")
p3_ok = False
if data3 is not None:
    cmd_ok = (data3[0] == 0x03)
    check("setAcquisitionParameters (packed,sm=0) cmd echo = 0x03",
          cmd_ok, f"got 0x{data3[0]:02X}" if not cmd_ok else "")
    err_ok = (b1_p3 == 0)
    check("setAcquisitionParameters (packed,sm=0) error_code = 0", err_ok,
          f"byte[1] = 0x{b1_p3:02X} ({b1_p3})" if not err_ok else "")
    p3_ok = cmd_ok and err_ok
    if p3_ok:
        print("  *** PACKED FACTORY-DEFAULT LAYOUT SUCCEEDED ***")
drain(dev)

# ── Determine working variant ─────────────────────────────────────────────────

working_payload = None
working_scanmode = None
working_exposure = None
if p2_ok:
    working_payload = padded_payload
    working_scanmode = 3
    working_exposure = 5000
    print("\n  Using Phase 2 (padded) payload for subsequent phases.")
elif p3_ok:
    working_payload = packed_payload
    working_scanmode = 0
    working_exposure = 10
    print("\n  Using Phase 3 (packed factory-default) payload for subsequent phases.")

# ── Phase 4: setFrameFormat + trigger + poll (only if a variant worked) ────────

print("\n=== Phase 4: setFrameFormat + triggerAcquisition + frame poll ===")
if working_payload is None:
    print("  skipped — no working setAcquisitionParameters variant found")
    _results.append(("setFrameFormat", False))
    _results.append(("frame ready", False))
else:
    drain(dev)
    sf_payload  = list(struct.pack('<H', 0))     # startElement=0
    sf_payload += list(struct.pack('<H', 3647))  # endElement=3647
    sf_payload += [0]                            # reductionMode=0
    print(f"  setFrameFormat packet bytes: {[f'0x{b:02X}' for b in [0x04] + sf_payload]}")
    write65(dev, 0x04, sf_payload)
    data = read_norm(dev, 500)
    if data is None:
        check("setFrameFormat: response received", False, "timeout")
    else:
        print(f"  raw response [0:8]: {[f'0x{b:02X}' for b in data[:8]]}")
        hexdump(data)
        cmd_ok = (data[0] == 0x04)
        err_ok = (data[1] == 0)
        check("setFrameFormat cmd echo = 0x04", cmd_ok,
              f"got 0x{data[0]:02X}" if not cmd_ok else "")
        check("setFrameFormat error_code = 0", err_ok,
              f"byte[1] = 0x{data[1]:02X} ({data[1]})" if not err_ok else "")
        if cmd_ok and len(data) >= 4:
            num_pixels = struct.unpack_from('<H', bytes(data), 2)[0]
            print(f"  numPixelsInFrame = {num_pixels}  {'(expect 3648)' if num_pixels == 3648 else ''}")
    drain(dev)

    print("\n  sending triggerAcquisition (0x06)")
    write65(dev, 0x06)
    frames = 0
    for poll in range(200):
        sleep(0.025)
        write65(dev, 0x01)
        sd = read_norm(dev, 200)
        if sd and sd[0] == 0x01:
            frames = struct.unpack_from('<H', bytes(sd), 2)[0]
            if frames > 0 or poll % 20 == 0:
                print(f"  poll {poll+1:3d} ({(poll+1)*25:4d} ms): "
                      f"statusFlags=0x{sd[1]:02X}  framesInMemory={frames}")
            if frames > 0:
                break
    else:
        print("  framesInMemory never > 0 after 5 s")
    check("frame ready within 5 s", frames > 0)

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
print(f"  Results: {passed}/{total} passed,  {total - passed} failed")
print()
if b1_p2 is not None:
    print(f"  clearMemory     byte[1] = 0x{b1 if b1 is not None else 0:02X}")
    print(f"  setAcqParams (padded 12B) byte[1] = 0x{b1_p2:02X} ({b1_p2})")
if b1_p3 is not None:
    print(f"  setAcqParams (packed,sm0) byte[1] = 0x{b1_p3:02X} ({b1_p3})")
print()
if p2_ok:
    print("  *** MSVC-padded layout works — update libspec.py to add 3 padding bytes ***")
elif p3_ok:
    print("  *** Packed factory-default layout works — check scanMode / exposureTime values ***")
else:
    print("  No setAcquisitionParameters variant worked.")
    print("  Compare byte[1] values between clearMemory and setAcquisitionParameters.")
    print("  If clearMemory byte[1]==0 but setAcqParams byte[1]==0x0D: payload-specific issue.")
    print("  If clearMemory byte[1]==0x0D too: ALL write commands are blocked.")
print("=" * 60 + "\n")

try:
    dev.close()
except Exception:
    pass

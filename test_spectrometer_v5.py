"""
Diagnostic test for ASQE Spectrometer — version 5.

Run:
    conda run --no-capture-output -n env38 python test_spectrometer_v5.py

v4 outcome
----------
  - write9(getStatus) returns 9 — write transport is confirmed working.
  - Response: 64 bytes, byte[0] = 0x0D (not the expected 0x81).
  - Feature reports: all return -1 — not supported by this device.
  - v4 only read ONCE after each command; we never saw what was queued behind 0x0D.

Root question: why is byte[0] = 0x0D instead of 0x81?

Hypotheses
----------
  A — 0x0D is an unsolicited/deferred packet from the device (30 ms flush missed it);
      actual 0x81 reply is queued immediately behind it.
      Clue against: v4 Phase 1 was a clean first-write, making stale state unlikely.

  B — 0x0D is the HID INPUT REPORT ID (HIDAPI prepends it on Windows).
      Real opcode is at byte[1].  byte[1] = 0x01, but expected 0x81
      → still unexplained unless the device also uses cmd-echo (not cmd|0x80).

  C — The device uses cmd-echo: responses do NOT set the high bit.
      byte[0] = fixed header 0x0D (report ID or prefix); byte[1] = cmd that was sent.
      Clue for: byte[1] = 0x01 = the exact getStatus opcode we sent.

This test
---------
  Phase 1 — Passive read: open device, read 5× WITHOUT sending anything.
             If 0x0D arrives → device broadcasts unsolicited (supports A).
             If silence → device speaks only when commanded (rules out A; focus on B/C).

  Phase 2 — Multi-read after getStatus: one write9(getStatus), then drain up to 10
             reads (200 ms each).  Full hex dump of every packet.
             Is 0x81 found anywhere?  If yes → stale packet (A confirmed).
             If 0x0D only → 0x0D IS the response (B or C).

  Phase 3 — Command pattern: send getStatus (0x01), getFrameFormat (0x08), and
             setFrameFormat (0x04, default full range).  For each, print byte[0]
             and byte[1] of the raw response.
             If byte[1] tracks the command opcode → Hypothesis C confirmed.
             If byte[1] tracks cmd|0x80      → Hypothesis B confirmed.
             If byte[0] is directly 0x81/0x88/0x84 → Hypothesis A confirmed.
"""

import sys
import struct
import traceback
from time import sleep

# ── Reporter ──────────────────────────────────────────────────────────────────

_results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    _results.append((label, condition))
    return condition


# ── HID helpers ───────────────────────────────────────────────────────────────

def write9(dev, opcode, payload=None):
    """Send 9 bytes: report-ID=0x00, opcode, zero-padded payload."""
    pkt = [0x00, opcode] + (payload or [])
    pkt = pkt[:9]
    pkt += [0x00] * (9 - len(pkt))
    return dev.write(pkt)


def raw_read(dev, timeout_ms=300):
    """Read from device, return exactly what HIDAPI gives (no stripping)."""
    raw = dev.read(65, timeout_ms)
    return list(raw) if raw else None


def hexdump(data, indent="    "):
    """Print full hex + ASCII, 16 bytes per row."""
    if not data:
        print(f"{indent}(empty)")
        return
    for row in range(0, len(data), 16):
        chunk = data[row:row + 16]
        hex_part = " ".join(f"{x:02X}" for x in chunk)
        asc_part = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"{indent}{row:04X}  {hex_part:<48}  {asc_part}")


def drain(dev, timeout_ms=200, max_reads=15, tag=""):
    """Drain input queue.  Returns count of packets found."""
    count = 0
    for _ in range(max_reads):
        pkt = raw_read(dev, timeout_ms)
        if pkt is None:
            break
        count += 1
        prefix = f"  drain{('/' + tag) if tag else ''} pkt {count}"
        print(f"{prefix}: {len(pkt)} bytes  b[0]=0x{pkt[0]:02X}")
        hexdump(pkt)
    return count


# ── Pre-flight ────────────────────────────────────────────────────────────────

print("\n=== Pre-flight ===")
try:
    import hid as _hid
    check("hid module is importable", True)
except ImportError as e:
    check("hid module is importable", False, str(e))
    sys.exit(1)

VID, PID = 0x20E2, 0x0001
all_devs = _hid.enumerate(VID, PID)
if not check(f"Device visible on USB (VID=0x{VID:04X}, PID=0x{PID:04X})", len(all_devs) > 0,
             "device not found — check USB cable"):
    sys.exit(1)

# ── Phase 1: Passive read ─────────────────────────────────────────────────────
# Open device, read without sending ANYTHING.
# Tests whether the device broadcasts unsolicited packets (Hypothesis A).

print("\n=== Phase 1: Passive read (no writes) ===")
print("  Open device, read 5× with 300 ms timeout each.  No writes.")

_p1_dev = _hid.device()
unsolicited_count = 0
try:
    _p1_dev.open(VID, PID)
    _p1_dev.set_nonblocking(False)

    for i in range(5):
        pkt = raw_read(_p1_dev, timeout_ms=300)
        if pkt is None:
            print(f"  read {i + 1}: timeout")
        else:
            unsolicited_count += 1
            print(f"  read {i + 1}: {len(pkt)} bytes  b[0]=0x{pkt[0]:02X}")
            hexdump(pkt)

    if unsolicited_count > 0:
        check("Device is silent on open (no unsolicited packets)", False,
              f"{unsolicited_count} packet(s) arrived without a write — device broadcasts on open")
        print("  → Hypothesis A SUPPORTED: 0x0D may be a broadcast; flush longer before reading.")
    else:
        check("Device is silent on open (no unsolicited packets)", True)
        print("  → Hypothesis A ruled out: device speaks only when commanded.")
        print("     0x0D is the actual response to our write (Hypothesis B or C).")

except Exception as e:
    print(f"  Phase 1 error: {e}")
    traceback.print_exc()
finally:
    try:
        _p1_dev.close()
    except Exception:
        pass

# ── Phase 2: Multi-read after getStatus ───────────────────────────────────────
# Send ONE getStatus.  Drain up to 10 responses (200 ms each).
# Full hex dump of every packet.  Is 0x81 anywhere?

print("\n=== Phase 2: Multi-read after getStatus ===")
print("  Send write9(getStatus=0x01), then drain up to 10 reads (200 ms each).")
print("  Looking for 0x81 anywhere in the response queue.")

_p2_dev = _hid.device()
found_81_at = None
try:
    _p2_dev.open(VID, PID)
    _p2_dev.set_nonblocking(False)

    # Long drain first to flush anything from connection
    print("  Pre-write drain (200 ms timeout):")
    n_pre = drain(_p2_dev, timeout_ms=200, max_reads=10, tag="pre")
    if n_pre == 0:
        print("  (nothing in queue before write)")

    ret = write9(_p2_dev, 0x01)
    print(f"\n  write9(getStatus=0x01) → returned {ret}")

    print("  Reading up to 10 packets after write:")
    for i in range(10):
        pkt = raw_read(_p2_dev, timeout_ms=200)
        if pkt is None:
            print(f"  read {i + 1}: timeout — queue empty")
            break
        b0 = pkt[0]
        b1 = pkt[1] if len(pkt) > 1 else 0
        print(f"  read {i + 1}: {len(pkt)} bytes  b[0]=0x{b0:02X}  b[1]=0x{b1:02X}")
        hexdump(pkt)
        if found_81_at is None and 0x81 in pkt[:4]:
            found_81_at = (i + 1, pkt[:4].index(0x81))
            print(f"  *** 0x81 found in read {i + 1} at byte position {found_81_at[1]} ***")

    if found_81_at:
        check("0x81 found in multi-read queue", True,
              f"at read {found_81_at[0]}, byte {found_81_at[1]}")
        print("  → Hypothesis A CONFIRMED: 0x81 was queued behind 0x0D.")
        print(f"     Fix: drain with ≥200 ms timeout before reading command response.")
    else:
        check("0x81 found in multi-read queue", False,
              "0x81 never appeared — 0x0D is the real response format")
        print("  → 0x0D is the actual response (not a stale packet).")
        print("     Hypothesis B or C is correct; see Phase 3.")

except Exception as e:
    print(f"  Phase 2 error: {e}")
    traceback.print_exc()
finally:
    try:
        _p2_dev.close()
    except Exception:
        pass

# ── Phase 3: Command pattern ──────────────────────────────────────────────────
# Send three different commands.  For each, record b[0] and b[1] of the raw response.
#
# Commands chosen:
#   0x01  getStatus      — no payload, read-only
#   0x08  getFrameFormat — no payload, read-only (opcode 0x08, reply would be 0x88 if cmd|0x80)
#   0x04  setFrameFormat — payload: start=0, end=3647, reductionMode=0 (safe, restores default)
#
# Expected byte[1] under each hypothesis:
#   Hypothesis A (stale packet):    b[0] = 0x81 / 0x88 / 0x84  directly
#   Hypothesis B (0x0D=report ID):  b[0] = 0x0D, b[1] = 0x81 / 0x88 / 0x84
#   Hypothesis C (cmd echo):        b[0] = 0x0D, b[1] = 0x01 / 0x08 / 0x04

print("\n=== Phase 3: Command pattern (byte[0] and byte[1] for 3 commands) ===")
print("  Hypothesis B: b[0]=0x0D, b[1]=cmd|0x80 (0x81, 0x88, 0x84)")
print("  Hypothesis C: b[0]=0x0D, b[1]=cmd      (0x01, 0x08, 0x04)")
print()

COMMANDS = [
    (0x01, "getStatus",      None),
    (0x08, "getFrameFormat", None),
    (0x04, "setFrameFormat", [0x00, 0x00,   # startElement = 0        (LE uint16)
                              0x3F, 0x0E,   # endElement   = 3647 = 0x0E3F (LE uint16)
                              0x00]),       # reductionMode = 0
]

_p3_dev = _hid.device()
pattern_rows = []
try:
    _p3_dev.open(VID, PID)
    _p3_dev.set_nonblocking(False)
    drain(_p3_dev, timeout_ms=200, max_reads=10, tag="init")

    for (cmd, name, payload) in COMMANDS:
        ret = write9(_p3_dev, cmd, payload)
        sleep(0.1)

        # Read first packet
        pkt = raw_read(_p3_dev, timeout_ms=300)

        if pkt is None:
            print(f"  cmd=0x{cmd:02X} ({name:16s}): write→{ret}  TIMEOUT")
            pattern_rows.append((cmd, name, None, None))
            continue

        b0 = pkt[0]
        b1 = pkt[1] if len(pkt) > 1 else 0

        if b1 == cmd:
            tag = "← cmd echo (Hyp C)"
        elif b1 == (cmd | 0x80):
            tag = "← cmd|0x80 (Hyp B)"
        elif b0 == (cmd | 0x80):
            tag = "← b[0] is reply opcode (Hyp A / no prefix)"
        else:
            tag = f"← unknown (b[0]=0x{b0:02X})"

        print(f"  cmd=0x{cmd:02X} ({name:16s}): write→{ret}  "
              f"b[0]=0x{b0:02X}  b[1]=0x{b1:02X}  {tag}")
        hexdump(pkt[:16])
        pattern_rows.append((cmd, name, b0, b1))

        # Drain leftover packets from multi-packet commands
        drain(_p3_dev, timeout_ms=50, max_reads=5)

    # Evaluate pattern
    print()
    valid = [(cmd, b0, b1) for (cmd, _, b0, b1) in pattern_rows if b0 is not None]
    if valid:
        all_b0_are_0D     = all(b0 == 0x0D for (_, b0, _) in valid)
        all_b1_cmd_echo   = all(b1 == cmd   for (cmd, _, b1) in valid)
        all_b1_cmd_hi_bit = all(b1 == (cmd | 0x80) for (cmd, _, b1) in valid)

        check("b[0] = 0x0D for all commands (prefix/report-ID present)", all_b0_are_0D)
        check("b[1] = cmd echo (no |0x80) — Hypothesis C",        all_b1_cmd_echo)
        check("b[1] = cmd|0x80           — Hypothesis B",         all_b1_cmd_hi_bit)

        print()
        if all_b0_are_0D and all_b1_cmd_echo:
            print("  CONCLUSION — Hypothesis C confirmed.")
            print("  The device response format is: [0x0D, cmd_echo, payload...]")
            print("  libspec.py _write_read() must NOT expect cmd|0x80.")
            print()
            print("  Required fixes to libspec.py:")
            print("    1. _normalize_response(): strip leading 0x0D byte from every response.")
            print("    2. _write_read(): expected_reply = cmd (not cmd|0x80).")
            print("    3. _write_read(): response payload starts at data[1] (not data[0]).")
            print("       e.g. getStatus: statusFlags at data[1], framesInMemory at data[2:4].")
        elif all_b0_are_0D and all_b1_cmd_hi_bit:
            print("  CONCLUSION — Hypothesis B confirmed.")
            print("  0x0D is the HID input report ID; payload starts at byte[1].")
            print("  libspec.py _normalize_response(): strip leading 0x0D byte.")
        elif not all_b0_are_0D:
            print("  CONCLUSION — Hypothesis A confirmed (or device uses no prefix).")
            print("  b[0] is the response opcode directly (cmd|0x80).")
            print("  Increase flush timeout in libspec.py _write_read() to ≥200 ms.")
        else:
            print("  CONCLUSION — Pattern unclear.  Inspect the hex dumps above.")

except Exception as e:
    print(f"  Phase 3 error: {e}")
    traceback.print_exc()
finally:
    try:
        _p3_dev.close()
    except Exception:
        pass

# ── Summary ───────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
total  = len(_results)
passed = sum(1 for _, ok in _results if ok)
failed = total - passed
print(f"  Results: {passed}/{total} passed,  {failed} failed")
print("""
  Quick reference — what to fix in libspec.py
  ─────────────────────────────────────────────
  Hyp A confirmed (b[0] = 0x81 directly):
    _write_read(): add drain(200 ms) before read; no other changes.

  Hyp B confirmed (b[0]=0x0D, b[1]=cmd|0x80):
    _normalize_response(): strip leading 0x0D byte on Windows.
    Everything else (expected_reply, data offsets) stays the same.

  Hyp C confirmed (b[0]=0x0D, b[1]=cmd):
    _normalize_response(): strip leading 0x0D byte.
    _write_read(): pass cmd as expected_reply (not cmd|0x80).
    All callers: response data starts at offset [1] not [0].
""")
print("=" * 60 + "\n")

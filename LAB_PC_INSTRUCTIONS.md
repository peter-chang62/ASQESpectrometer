# Lab PC Instructions — ASQE Spectrometer Python Driver

**For:** Claude Code session on the lab PC (Windows) connected to the ASQE Spectrometer  
**Prepared by:** Claude Code session on the author's Mac (2026-04-13)  
**Status:** Implementation complete, ready for hardware testing

---

## What This Project Is

Python driver for the ASQE UV-Vis spectrometer (3648-element CCD, ~243–952 nm).
The main library is `libspec.py`. Three example scripts exist:

| Script | What it plots |
|---|---|
| `spectrum.py` | Normalized intensity (a.u.), real-time |
| `spectrum_norm.py` | Power spectral density (uW/cm²/nm), real-time |
| `spectrum_calib.py` | Normalized intensity (same as spectrum.py — see Known Issues) |

---

## What Just Changed (and Why)

### The problem

The spectrometer ships with a native C DLL (`lib/libspectr64bit.dll`). The original
`libspec.py` loaded that DLL via ctypes. The DLL has a bug: it hardcodes the USB
Vendor ID and Product ID **byte-swapped**:

```
DLL expects:   VID=0xE220, PID=0x0100   ← wrong (bytes swapped)
Device is:     VID=0x20E2, PID=0x0001   ← what Windows actually sees
```

This caused `connectToDevice()` to always return error code **502** (connection failed).
No compiler is available to rebuild the DLL.

### The fix

`libspec.py` has been **completely rewritten** to bypass the DLL. It now talks directly to
the USB HID device using Python's `hid` module (`pip install hid`), using the correct
VID/PID. The DLL files in `lib/` are no longer used.

All public methods (`configure_acquisition`, `capture_frame`, `normalize_spectrum`, etc.)
have **identical signatures** to before — `spectrum.py` and the other example scripts
work unchanged.

---

## Environment Setup

### 1. Verify `hid` is installed

```
pip install hid
```

Or check: `python -c "import hid; print(hid.__version__)"` — should print `0.15.0` or similar.

If `hid` is missing from `requirements.txt`, add it:
```
hid>=0.14.0
```

### 2. Close LabView (critical)

Only one process can hold the HID device at a time. If LabView (or any other tool)
has the spectrometer open, Python will get a `hid.HIDException: open failed`.

**Close LabView before running any Python code.**

### 3. Verify device is visible

```python
import hid
print(hid.enumerate(0x20E2, 0x0001))
```

Should print a list with one entry. If empty: check USB cable, check LabView is closed.

### 4. Windows permissions

On most Windows installations, HID devices are accessible without admin rights.
If you get a permission error, try running the terminal as Administrator once.

---

## Running the Tests

```
python test_spectrometer.py
```

The test script runs 6 phases in order and prints `[PASS]` / `[FAIL]` for each check.
It exits early if connection fails (Phase 1), so fix connection issues first.

Expected output (all passing):

```
=== Pre-flight Checks ===
  [PASS] hid module is importable
  [PASS] ASQE Spectrometer visible on USB (VID=0x20E2, PID=0x0001)

=== Phase 1: Connection ===
  [PASS] ASQESpectrometer() creates without exception
  [PASS] _dev is not None
  [PASS] _num_pixels_in_frame is 0 before configure_acquisition

=== Phase 2: configure_acquisition ===
  [PASS] configure_acquisition() raises no exception
  [PASS] _num_pixels_in_frame == 3648 (default full range, no binning)
  [PASS] reduction_mode=1 → _num_pixels_in_frame == 1824
  [PASS] reset to reduction_mode=0 restores 3648 pixels

=== Phase 3: capture_frame ===
  [PASS] capture_frame() raises no exception
  [PASS] len(buf) == 3694
  [PASS] buf.dtype == np.uint16
  [PASS] buf contains non-zero values (device is returning data)

=== Phase 4: subtract_background / normalize_spectrum ===
  [PASS] subtract_background() raises no IndexError
  [PASS] subtract_background() returns 3653 elements
  ...

=== Phase 5: get_calibrated_spectrum ===
  ...

=== Phase 6: Flash read ===
  (reading full calibration file — may take ~30 s) ...
  ...
```

Phase 6 (flash read) is slow (~30 s) because it reads up to 100 KB from the device in
1000-byte chunks.

---

## Troubleshooting

### `hid.HIDException: open failed`
→ LabView or another process has the device. Close it and retry.

### `HID read timeout after 100 ms (error 505)` during `configure_acquisition`
→ The device accepted the write but didn't reply in time. Try:
1. Increase the timeout: edit `_write_read` call in `configure_acquisition()` to pass `timeout_ms=500`.
2. Unplug and re-plug the USB cable, then retry.
3. Check if the device needs a firmware warm-up period after power-on.

### `Wrong reply opcode: expected 0x83, got 0x??`
→ The device replied with an unexpected opcode. This may mean the device is in an
unexpected state. Try calling `spec._write(0xF1)` (resetDevice, no reply) to factory-reset
the device, then reconnect.

### `getFrame: packet count mismatch (error 507)`
→ A frame packet arrived with an unexpected `packetsRemaining` counter. Usually caused
by a partial prior transmission. Reconnect and retry.

### Phase 6 fails: `readFlash: wrong reply`
→ Flash reads are sensitive to timing. The device might be busy. Add a short `sleep(0.1)`
before the `read_flash()` call in `read_calibration_file()`.

### `capture_frame` returns all zeros
→ The frame was captured but the CCD received no light (or `scan_mode=3` averaged nothing).
- Confirm the exposure_time is long enough: default is 1000 (= 10 ms). Try 10000 (100 ms).
- Confirm `scan_mode=3` with `num_of_scans=1` is correct for your use case.

---

## Device Protocol Reference (for debugging)

All communication is USB HID, 65-byte packets (little-endian integers).

```
SEND (65 bytes):  [0x00][opcode][payload...][0x00-padded]
RECV (64 bytes):  [reply_opcode][data...]

Key opcodes:
  0x01 → 0x81  getStatus         recv: [0x81][flags][framesL][framesH]
  0x03 → 0x83  setAcqParams      send: [scansL/H][blanksL/H][mode][t0..t3]
  0x04 → 0x84  setFrameFormat    send: [startL/H][endL/H][reduce]   recv: [err][pixL/H]
  0x06         triggerAcq        (write-only, no reply)
  0x08 → 0x88  getFrameFormat    recv: [startL/H][endL/H][reduce][pixL/H]
  0x0A → 0x8A  getFrame          multi-packet: 30 pixels per packet
  0x1A → 0x9A  readFlash         multi-packet: 60 bytes per packet, 100-packet burst max
  0xF1         resetDevice       (write-only, factory reset, no reply)
```

VID = 0x20E2, PID = 0x0001 (corrected — the DLL had these byte-swapped)

To manually verify the device protocol from a Python REPL:
```python
import hid
d = hid.device()
d.open(0x20E2, 0x0001)
d.set_nonblocking(False)

# Send getStatus
pkt = [0x00, 0x01] + [0x00] * 63
d.write(pkt)
print(d.read(65, 500))  # expect [0x81, flags, framesL, framesH, ...]

d.close()
```

---

## Known Issues (pre-existing, not introduced by this rewrite)

| Issue | Location | Detail |
|---|---|---|
| Wrong method call | `spectrum_norm.py` line 33 | Calls `get_calibrated_spectrum()` instead of `normalize_spectrum()` — despite the filename suggesting normalized output. Y-axis label says "uW/cm²/nm" so calibrated is probably correct. |
| Wrong method call | `spectrum_calib.py` line 32 | Calls `normalize_spectrum()` — despite filename suggesting calibrated output. Mismatch between filename and behavior. |
| README typo | `README.md` line 125 | Imports from `"libspectr"` instead of `"libspec"`. |
| Background subtraction uses partial dark pixels | `subtract_background()` | Indices `[3686:3692]` fall outside the default 3648-pixel range, so they are zero. `devd2 = 0` and the background is halved relative to a true dual-sided estimate. This was the same behavior in the original DLL-based code. |
| exposure_time default mismatch | `libspec.py` vs device | Python default is 1000 (10 ms); device factory default is 10 (100 µs). Device resets to 10 after `resetDevice()`. |

---

## Running the Example Scripts (after tests pass)

```
python spectrum.py           # normalized intensity, real-time plot, press Q to quit
python spectrum_norm.py      # power-calibrated (uW/cm²/nm), real-time
python spectrum_calib.py     # also normalized (see Known Issues above)
```

All scripts call `spec.configure_acquisition()` on startup and loop until 1000 measurements
or the user presses Q.

---

## Key Files

| File | Purpose |
|---|---|
| `libspec.py` | Main driver — **pure Python HID, DLL no longer used** |
| `test_spectrometer.py` | Integration test script |
| `requirements.txt` | Python dependencies (add `hid>=0.14.0` if missing) |
| `reimplementation_plan.md` | Full protocol reference derived from `libspectr.c` source |
| `CLAUDE.md` | Architecture reference (device params, error codes, flash layout) |
| `lib/libspectr64bit.dll` | Old DLL — kept for reference, no longer loaded |
| `All software to download/DLL_source_code/libspectr.c` | C source — authoritative protocol reference |

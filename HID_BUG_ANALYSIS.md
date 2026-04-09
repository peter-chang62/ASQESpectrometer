# HID Communication Bug Analysis

## Summary

`spectrum.py` fails on Windows 11 with `RuntimeError: Unexpected HID reply: got 0x0D, expected 0x83`. The root cause is a Windows 11 HID driver enforcement change that prevents sending the 64-byte command packets the device requires.

---

## Device Info

| Property | Value |
|----------|-------|
| Manufacturer | ASEQ Instruments |
| Product | mini-Spectrometers solution |
| VID | `0x20E2` |
| PID | `0x0001` |
| Serial | `1.0.0` |
| HID Input report | 64 bytes |
| HID Output report | **8 bytes** |

---

## Root Cause 1: Windows 11 HID Output Report Size Enforcement

### The problem

The device's HID report descriptor defines an **8-byte output report** (host → device) and a 64-byte input report (device → host):

```
Output: Report Size=8, Report Count=8  →  8 bytes
Input:  Report Size=8, Report Count=32 →  32 bytes (×2 = 64 bytes total)
```

Confirmed via `HidP_GetCaps`:
```
InputReportByteLength  = 65  (64 data + 1 report ID)
OutputReportByteLength =  9  (8 data + 1 report ID)
```

The protocol (`libspectr.c`) requires **64-byte command packets**. Every call to `_write()` in `libspec.py` tries to send a 65-byte buffer (1 report ID + 64 data bytes).

### Windows 11 enforcement

On Windows 11, `hidclass.sys` strictly caps `WriteFile` to `OutputReportByteLength` regardless of how many bytes you pass:

```
WriteFile(handle, 65-byte-packet, 65) → bytes_written = 9  (always)
WriteFile(handle, 65-byte-packet, 64) → bytes_written = 9  (always)
WriteFile(handle, 9-byte-packet,   9) → bytes_written = 9  (success)
WriteFile(handle, 8-byte-packet,   8) → bytes_written = 0  (error 87)
```

This was tested with both synchronous and overlapped I/O — both cap at 9 bytes.

### Why the old DLL worked (on older Windows)

The bundled `hid.c` (signal11/hidapi, ~2009) had this logic in `hid_write()`:

```c
if (length >= dev->output_report_length) {
    buf = (unsigned char *) data;   // use full buffer as-is
} else {
    // pad to output_report_length
}
res = WriteFile(dev->device_handle, buf, length, NULL, &ol);
```

When `length=65 >= output_report_length=9`, it called `WriteFile(handle, data, 65)`. On **Windows 7**, the HID class driver passed all 65 bytes through to the USB interrupt OUT endpoint. On **Windows 11**, the same call is silently truncated to 9 bytes.

Modern `cython-hidapi` (v0.15.0, used in env38) has the opposite logic — it explicitly uses `output_report_length` as the write size, so it always sends exactly 9 bytes.

### Device response to truncated packets

When the device receives only 8 bytes instead of 64, it responds with error code `0x0D`, echoing the command byte at position 1:

```
STATUS cmd (0x01)        → [0x0D, 0x01, 0x00, 0x00, ...]
setAcquParams cmd (0x03) → [0x0D, 0x03, 0x0D, 0x01, 0x02, 0x03, ...]
```

`0x0D` is not a defined protocol response code (valid codes are `0x81`–`0x8C`, `0x9A`–`0x9C`). It appears to be a device-firmware error for "incomplete/invalid packet."

---

## Root Cause 2: DLL Compiled with Wrong VID/PID

`libspectr64bit.dll` fails with error 502 (`CONNECT_ERROR_FAILED`) because it was compiled with the byte-swapped VID/PID values from the C source:

| | VID | PID |
|-|-----|-----|
| C source (`libspectr.c`) | `0xE220` | `0x0100` |
| Compiled DLL binary | `0xE220` | `0x0100` |
| **Actual device** | **`0x20E2`** | **`0x0001`** |

Confirmed by searching the DLL binary for both byte patterns — the correct values (`0x20E2`) are absent.

Even with the correct VID/PID, the DLL would still fail on Windows 11 due to Root Cause 1.

---

## How the Read Side Works (Correct)

The old hidapi read logic (relevant to understanding `data[0]`):

```c
if (dev->read_buf[0] == 0x0) {
    // Windows prepended a null report ID — strip it
    memcpy(data, dev->read_buf + 1, copy_len);
} else {
    // Non-zero first byte: include it (it's real data or a report ID)
    memcpy(data, dev->read_buf, copy_len);
}
```

Since the device sends `0x0D` as the first byte (not `0x00`), the report ID is **not** stripped — `data[0] = 0x0D` is the genuine first byte from the device. The 64-byte input report is structurally correct.

---

## Fix Options

### Option A — WinUSB via Zadig (recommended)

Install [Zadig](https://zadig.akeo.ie/), select the ASEQ spectrometer, replace the HID driver with **WinUSB** or **libusbK**. Then rewrite `libspec.py`'s write path using `pyusb`:

```python
import usb.core

dev = usb.core.find(idVendor=0x20E2, idProduct=0x0001)
# find interrupt OUT endpoint
ep_out = ...
ep_out.write(packet)   # sends full 64 bytes, no HID size limit
```

This completely bypasses `hidclass.sys`. After switching drivers, verify with:
```python
dev = usb.core.find(idVendor=0x20E2, idProduct=0x0001)
# send STATUS command, expect data[0] == 0x81
```

**Downside**: the device will no longer appear as HID; any HID-dependent tools will stop working.

### Option B — Test on Linux/Mac

On Linux, `hidapi` uses the `hidraw` kernel interface which does **not** enforce the output report size. The current `libspec.py` code (using `hid.write(65 bytes)`) should work without modification.

### Option C — Contact ASEQ

Request:
- A DLL compiled with correct VID/PID (`0x20E2 / 0x0001`) for Windows 10/11
- Clarification on whether a WinUSB firmware variant or updated HID descriptor (64-byte output report) is available

### Option D — Downgrade cython-hidapi

An older `hidapi` build (pre-`output_report_length` enforcement, roughly pre-2021) would call `WriteFile(handle, data, 65)`. On Windows 11 this still sends only 9 bytes at the driver level, so **this does not fix the problem** — it only removes the Python-layer cap.

---

## Diagnostic Scripts

All scripts run under `conda run -n env38 python <script>`.

| Script | Purpose |
|--------|---------|
| `debug_hid.py` | Enumerate device, raw write/read, flush stale packets |
| `debug_write.py` | Confirm `hid.write()` truncation with bytes vs list |
| `debug_writefile.py` | Direct `WriteFile` tests with various sizes |
| `debug_interfaces.py` | Decode HID report descriptor, `HidP_GetCaps` output |
| `debug_feature.py` | Test `HidD_SetFeature`, `HidD_SetOutputReport`, `HidP_GetCaps` |
| `debug_protocol.py` | Controlled flush → write → wait → read tests |
| `debug_overlapped.py` | Overlapped `WriteFile` test (matches old hidapi pattern) |
| `debug_overlapped2.py` | Fixed OVERLAPPED struct, confirms bytes_written=9 always |
| `debug_dll.py` | Confirms DLL returns error 502 (`CONNECT_ERROR_FAILED`) |

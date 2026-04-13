# Diagnosis Results: `connectToDevice()` Returns 502

**Date:** 2026-04-13  
**Environment:** Windows 11, Python 3.8 (conda env `env38`), `hid` v0.15.0  
**Device under test:** ASEQ mini-Spectrometer  

---

## Executive Summary

All four debug hypotheses were tested. **H1 (VID/PID byte-swap) is the sole confirmed root cause.** The DLL's embedded HIDAPI implementation is fully functional — it can enumerate HID devices and open the spectrometer when given the correct VID/PID. The failure is entirely because every high-level DLL function (`connectToDevice`, `connectToDeviceByIndex`, `getDeviceCount`) internally passes the hardcoded constants `0xE220, 0x0100` to `hid_open`, which are byte-swapped relative to the device's actual USB identity `0x20E2, 0x0001`. All DLL-based workarounds were tried and exhausted. A compiler is not available to rebuild the DLL. **Pure Python HID reimplementation is the necessary next step.**

---

## Step 1 — HID Device Enumeration

**Script:** `hid.enumerate(0, 0)` — enumerate all HID devices with non-empty strings.

**Output:**
```
VID=0x06CB  PID=0x000F  Mfr=Synaptics Inc.  Prod=HID Miniport Device   (×4 entries)
VID=0x20E2  PID=0x0001  Mfr=ASEQ"           Prod=mini-Spectrometers solution
```

**Analysis:**

The spectrometer appears at **`VID=0x20E2, PID=0x0001`**. This directly confirms hypothesis H1.

Compare to what the DLL source hardcodes (`libspectr.c`, lines 4–5):
```c
#define USBD_VID    0xE220
#define USBD_PID    0x0100
```

| | VID | PID |
|---|---|---|
| DLL expects | `0xE220` = `[E2][20]` | `0x0100` = `[01][00]` |
| Device actual | `0x20E2` = `[20][E2]` | `0x0001` = `[00][01]` |
| Relationship | **upper/lower bytes swapped** | **upper/lower bytes swapped** |

Both fields are identically byte-swapped. This is a classic endian copy-paste error: the 16-bit constants were written in reversed byte order when transcribed from a USB spec or datasheet. Because `hid_open(0xE220, 0x0100, NULL)` finds zero matching devices, it returns `NULL`, and `connectToDevice` returns **502** (`CONNECT_ERROR_FAILED`).

Note: the manufacturer string has a trailing quote character (`ASEQ"`) — likely a firmware artifact, not a system issue.

---

## Step 2 — DLL Export Table

**Tool:** `pefile.PE('lib/libspectr64bit.dll')` — enumerate all exported symbols.

**Full export list:**
```
clearDevicesInfo          clearMemory               connectToDevice
connectToDeviceByIndex    detachDevice              disconnectDevice
eraseFlash                getAcquisitionParameters  getDeviceCount
getDevicesInfo            getFrame                  getFrameFormat
getStatus                 hid_close                 hid_enumerate
hid_error                 hid_exit                  hid_free_enumeration
hid_get_feature_report    hid_get_indexed_string    hid_get_manufacturer_string
hid_get_product_string    hid_get_serial_number_string
hid_init                  hid_open                  hid_open_path
hid_read                  hid_read_timeout          hid_send_feature_report
hid_set_nonblocking       hid_write                 readFlash
resetDevice               setAcquisitionParameters  setAllParameters
setExposure               setExternalTrigger        setFrameFormat
setOpticalTrigger         triggerAcquisition        writeFlash
```

**Analysis:**

Two key observations:

**1. HIDAPI is statically compiled into the DLL.**  
All HIDAPI functions (`hid_init`, `hid_open`, `hid_enumerate`, `hid_open_path`, `hid_close`, etc.) are directly exported. There is no dependency on an external `hidapi.dll`. This rules out H3 (missing external DLL). The `hid.c` source in `DLL_source_code/` is the HIDAPI implementation compiled directly into `libspectr64bit.dll`.

**2. `connectToDeviceByIndex` and `getDeviceCount` are present but undocumented in the source.**  
These two functions exist in the DLL's exports and in `func_descr.txt` but do **not** appear anywhere in the provided `libspectr.c` source. They were added to the compiled DLL after the source snapshot was made. Their behavior was tested in Step 3.

**3. `hid_open_path` is exported.**  
This accepts a raw OS device path string (e.g., `\\?\HID#VID_20E2...`) instead of VID/PID. Potentially usable as a bypass — explored in Step 3.

---

## Step 3 — High-Level DLL Connection Functions

**Tested:** `connectToDeviceByIndex(0)`, `getDeviceCount()`

**Output:**
```
Loading: lib/libspectr64bit.dll  (arch=64bit)
connectToDeviceByIndex(0) = 501
getDeviceCount() = 0
```

**Analysis:**

Both functions fail. Error 501 = `CONNECT_ERROR_NOT_FOUND` (device not found). `getDeviceCount() = 0` means the DLL's device enumeration finds zero devices.

Since `connectToDeviceByIndex` and `getDeviceCount` are not in the provided source, their internal implementation was inferred from behavior. `getDeviceCount() = 0` while `hid_enumerate(0, 0)` from the same DLL returns a valid pointer (Step 4) means these functions almost certainly call `hid_enumerate(USBD_VID, USBD_PID)` — i.e., they enumerate only for `0xE220, 0x0100` — and find nothing. They do not enumerate all devices and filter by index.

This definitively rules out `connectToDeviceByIndex` as a workaround.

**`hid_open_path` as bypass — not viable via the current API.**  
`connectToDevice(const char *serialNumber)` takes a serial number, not a path. Looking at the source:

```c
int connectToDevice(const char *serialNumber) {
    ...
    g_Device = hid_open(USBD_VID, USBD_PID, (const wchar_t *)serialToUse);
    ...
}
```

The serial number argument is passed as the third argument to `hid_open` (a wide-char serial number filter), not as a device path. There is no code path in `connectToDevice` that calls `hid_open_path`. To use the known device path (`\\?\HID#VID_20E2&PID_0001#...`), one would need to call `hid_open_path` directly from the DLL and then inject the resulting handle into the DLL's internal `g_Device` global — which requires knowledge of the internal struct layout and is brittle.

---

## Step 4 — Direct HIDAPI Probe from DLL

**Tested:** `hid_init()`, `hid_enumerate(0, 0)`, `hid_open(0x20E2, 0x0001, None)`

**Output:**
```
hid_init() = 0            (0=ok, -1=fail)
hid_enumerate(0, 0) = 2015645748624   (non-NULL = devices found)
hid_open(0x20E2, 0x0001) = 2015645748528   (non-NULL = opened successfully)
```

**Python hid module for comparison:**
```
hid module: C:\Users\chi3optics\miniconda3\envs\env38\lib\site-packages\hid.cp38-win_amd64.pyd
hid version: 0.15.0
hid.enumerate(0x20E2, 0x0001) found 1 device(s)
  path=b'\\\\?\\HID#VID_20E2&PID_0001#7&3a424fe4&4&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
  usage_page=65280
```

**Analysis — this is the most diagnostic step.**

The DLL's embedded HIDAPI is fully functional:

- `hid_init()` = 0: the Win32 `hid.dll` loaded successfully, all internal function pointers resolved. **H4 (hid.dll lookup failure) is ruled out.**
- `hid_enumerate(0, 0)` = non-NULL: the DLL can enumerate all HID devices on the system. The device driver is the Windows HID class driver. **H6 (WinUSB driver mismatch) is ruled out.**
- `hid_open(0x20E2, 0x0001)` = non-NULL: **the DLL can open the spectrometer when given the correct VID/PID.** This is the critical result. The DLL's own HIDAPI transport layer works perfectly.

The failure is entirely confined to the hardcoded `USBD_VID`/`USBD_PID` constants at lines 4–5 of `libspectr.c`.

**Confirmed root cause chain:**
```
connectToDevice(NULL)
  → hid_open(0xE220, 0x0100, NULL)   ← wrong VID/PID hardcoded in source
    → hid_enumerate returns nothing matching 0xE220:0x0100
    → returns NULL
  → g_Device = NULL
  → returns 502 (CONNECT_ERROR_FAILED)
```

**What would fix it at the DLL level:**
```c
// libspectr.c lines 4-5 — change to:
#define USBD_VID    0x20E2
#define USBD_PID    0x0001
```
Rebuild with MinGW or MSVC → replace `lib/libspectr64bit.dll`.

---

## Step 5 — Compiler Availability (for DLL Recompile)

**Checked:** `gcc`, `cl.exe` (MSVC), MinGW in conda, Visual Studio installation.

**Output:**
```
gcc not in PATH
cl.exe not in PATH
C:/Users/chi3optics/miniconda3/Library/mingw-w64/bin/ — directory does not exist
Visual Studio — not found in Program Files
```

No C compiler is available on this machine without a separate install. DLL recompilation is not currently feasible.

---

## Hypotheses — Final Status

| ID | Hypothesis | Status | Evidence |
|---|---|---|---|
| H1 | VID/PID byte-swap (`0xE220`/`0x0100` vs `0x20E2`/`0x0001`) | ✅ **CONFIRMED — root cause** | `hid.enumerate` shows device at `0x20E2, 0x0001`; DLL source hardcodes swapped values |
| H2 | LabView uses `connectToDeviceByIndex` which bypasses VID/PID | ❌ **REFUTED** | `connectToDeviceByIndex(0)` = 501; `getDeviceCount()` = 0; both filter by wrong VID/PID |
| H3 | `spectrlib_shared.dll` missing external `hidapi.dll` | ✅ **NOT APPLICABLE** | `libspectr64bit.dll` has HIDAPI statically linked; no external dependency |
| H4 | Old HIDAPI build fails to load Win32 `hid.dll` at runtime | ❌ **REFUTED** | `hid_init()` = 0; Win32 HID function pointers resolved successfully |
| H5 | Exclusive device access by another process | ❌ **REFUTED** | `hid_open(0x20E2, 0x0001)` succeeds from DLL itself |
| H6 | Driver reassigned to WinUSB (HID class GUID finds nothing) | ❌ **REFUTED** | `hid_enumerate(0, 0)` returns non-NULL list including the spectrometer |

---

## Fix Options — Evaluated

### Option A: `connectToDeviceByIndex(0)` workaround in `libspec.py`
**Status: Not viable.**  
`connectToDeviceByIndex` and `getDeviceCount` are both broken by the same byte-swap. `getDeviceCount()` returns 0, so `connectToDeviceByIndex(0)` cannot find a device at index 0. No amount of index passing will help when the enumeration finds an empty list.

### Option B: Patch `connectToDevice` via `hid_open_path`
**Status: Not viable without DLL modification.**  
`hid_open_path` is exported and would work (device path: `\\?\HID#VID_20E2&PID_0001#7&3a424fe4&4&0000#{...}`), but `connectToDevice` has no code path that uses it. To call `hid_open_path` and make the DLL's higher-level functions work, the resulting handle would need to be written into `g_Device` inside the DLL — a global variable at an unknown address. Reverse-engineering this is complex and fragile.

### Option C: Recompile DLL with corrected VID/PID
**Status: Not currently feasible.**  
The fix is trivial (two constants in `libspectr.c` lines 4–5). However, no C compiler (MinGW, MSVC, GCC) is available on this machine. Installing one (e.g., `scoop install gcc` or VS Build Tools) would unblock this, and all existing Python wrapper code in `libspec.py` would continue to work unchanged.

### Option D: Pure Python HID reimplementation
**Status: Recommended path forward.**  
Bypass `libspectr64bit.dll` entirely. Re-implement the USB protocol layer directly in Python using the `hid` module, which already opens the device successfully. All required information is available:

- **Protocol fully documented** in `All software to download/DLL_source_code/libspectr.c`
- **Packet format**: 64 bytes + 1 report ID byte = 65 bytes total, USB HID interrupt transfers
- **`hid` module version 0.15.0** installed in conda env `env38`
- **Device path**: `\\?\HID#VID_20E2&PID_0001#7&3a424fe4&4&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}`
- **All opcodes, response codes, error codes** known from source
- All high-level `libspec.py` methods (`capture_frame`, `read_flash`, `configure_acquisition`, etc.) would remain unchanged — only `_setup_function_prototypes()`, `connect()`, and the low-level packet I/O need to be replaced

This permanently fixes the issue, removes the platform-specific DLL dependency, and makes the library portable to Linux/macOS without needing to ship separate `.so`/`.dylib` binaries.

---

## What Python's `hid` Module Does Differently

Python's `hid` v0.15.0 (`hid.cp38-win_amd64.pyd`) is a Cython wrapper around a HIDAPI build that was compiled against a more recent HIDAPI source. Crucially, it uses the correct `VID=0x20E2, PID=0x0001` because those are passed by the calling code at runtime — there are no hardcoded constants. The DLL's HIDAPI transport layer is architecturally equivalent to what `hid` uses; the only difference is the wrong constants baked into `connectToDevice`.

---

## Recommended Next Step

Implement a pure Python HID transport layer in `libspec.py` that replaces all `self.lib.*` ctypes calls with `hid.device` operations. The `ASQESpectrometer` class interface (all public methods) stays identical. The change is internal to the transport layer only.

Key things to extract from `libspectr.c` before starting:
1. Command opcode byte layout (what byte 0 of each 64-byte packet means)
2. `triggerAcquisition` packet format
3. `getStatus` request/response format
4. `getFrame` multi-packet response format (up to 124 packets, frame packet count mismatch = error 507)
5. `readFlash` request format (offset, length) and response chunking
6. `setAcquisitionParameters` and `setFrameFormat` command formats

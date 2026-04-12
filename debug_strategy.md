# Debug Strategy: `connectToDevice()` Returns 502

## Situation

`python spectrum.py` fails because `self.lib.connectToDevice(None)` in `libspec.py` always returns
error code **502** (`CONNECT_ERROR_FAILED`). This means the native DLL called `hid_open()` and it
returned `NULL`. Meanwhile, Python's own `hid` library successfully opens the same device.
LabView examples also work. The EXE program fails like Python does.

---

## Verification of User's Observation

The user's observation about the DLL architecture is **correct and important**, but it's only part
of the picture. Here is what the source code actually reveals:

### LabView vs EXE/Python: HIDAPI split

The `lib/` folder contains `libspectr.dll`, `libspectr64bit.dll`, `libspectr.so`, and
`libspectr.dylib`. The LabView examples folder contains `hidapi_32bits.dll` and
`hidapi_64bits.dll` *separately*. This confirms two distinct DLL arrangements:

| Environment | Spectrometer DLL | HIDAPI |
|---|---|---|
| LabView examples | `spectrlib_shared.dll` | External `hidapi_XXbits.dll` in same folder |
| EXE program | `spectrlib_shared.dll` | Unknown — may need `hidapi.dll` in PATH or CWD |
| Python (this repo) | `lib/libspectr.dll` | Likely statically compiled in from `hid.c` |

The `hid.c` source in `DLL_source_code/` is **Windows-only** (top-level `#include <windows.h>`).
It uses Win32 API (`SetupDi*`, `CreateFile`, `HidD_*`) and dynamically loads `hid.dll` (the
Windows system HID library) at runtime via `LoadLibraryA("hid.dll")`. It does NOT use libusb or
WinUSB.

Python's `hid` module (cython-hidapi or similar) may use a different backend — potentially WinUSB
or a more recent HIDAPI version — depending on how it was installed.

---

## Critical Evidence: VID/PID Byte-Swap Discrepancy

This is the strongest lead. The C source hardcodes:

```c
// libspectr.c, lines 4-5
#define USBD_VID    0xE220
#define USBD_PID    0x0100
```

The user successfully opened the device with Python's `hid` module at:

```python
h.open(0x20E2, 0x0001)
```

Compare byte-by-byte:
- `0xE220` → bytes `[E2, 20]` vs `0x20E2` → bytes `[20, E2]` — **upper/lower bytes swapped**
- `0x0100` → bytes `[01, 00]` vs `0x0001` → bytes `[00, 01]` — **upper/lower bytes swapped**

These are exactly byte-swapped from each other. If the device's true USB VID/PID is `0x20E2, 0x0001`,
then the DLL's `hid_open(0xE220, 0x0100, NULL)` will enumerate zero matching devices and return
`NULL` → error 502. This would perfectly explain the failure.

**Why LabView works despite possibly having the same wrong VID/PID in the DLL:**

The `func_descr.txt` file documents two undocumented API functions at the bottom:

```
int connectToDeviceByIndex(unsigned int index);
    connect device with index. If only one device is connected use 0 as an index.

int getDeviceCount();
    returns number of connected devices
```

The LabView VI named `connect_to _device.vi` (note the space in the filename — unusual) likely
calls `connectToDeviceByIndex(0)` rather than `connectToDevice(NULL)`. If
`connectToDeviceByIndex` enumerates **all** HID devices regardless of VID/PID (or uses a
different VID/PID), it would succeed even when `connectToDevice` fails. This would explain the
exact split between LabView (works) and Python/EXE (fails).

Alternatively: the `hidapi_XXbits.dll` in the LabView folder is a different build that may have
correct VID/PID baked in, while the `lib/libspectr.dll` used by Python was compiled from the same
source that has the byte-swapped constants.

---

## Root Cause Hypotheses (ranked by probability)

### H1 — VID/PID Mismatch [HIGH]

The compiled DLL calls `hid_open(0xE220, 0x0100, NULL)`, but the device's actual USB VID/PID is
`0x20E2, 0x0001`. The enumeration finds nothing → NULL → 502. The byte-swap pattern is not
random; it looks like a classic copy-paste error where the 16-bit constant was written in
reversed byte order.

**Test**: On the lab PC with the device connected:
```python
import hid
for d in hid.enumerate(0, 0):
    print(f"  VID=0x{d['vendor_id']:04X}  PID=0x{d['product_id']:04X}  "
          f"Mfr={d['manufacturer_string']}  Prod={d['product_string']}")
```
Find the spectrometer entry. If its VID/PID is `0x20E2, 0x0001` (not `0xE220, 0x0100`), this
hypothesis is confirmed.

### H2 — LabView Uses `connectToDeviceByIndex` Instead of `connectToDevice` [HIGH]

Even if H1 is true, this explains the LabView/Python split. `connectToDeviceByIndex(0)` might
enumerate by index within a differently-filtered device list.

**Test**: Try calling this function from Python (see Debug Steps section).

### H3 — `spectrlib_shared.dll` Requires `hidapi.dll` in Same Directory [MEDIUM]

`spectrlib_shared.dll` (used by LabView) may be dynamically linked against a separate
`hidapi.dll`, which only exists in the LabView folder. The EXE program folder does not have this
DLL, so Windows falls back to a system version (or fails). `lib/libspectr.dll` in this Python
project is a different binary that likely has HIDAPI statically compiled in.

**Test**: Run `dumpbin /dependents "All software to download\EXE program\spectrlib_shared.dll"`
on Windows. Check if `hidapi.dll` is listed. If yes, check whether it exists alongside the EXE.

### H4 — System `hid.dll` Lookup Failure in Old HIDAPI Build [LOW-MEDIUM]

The `hid.c` source dynamically loads `hid.dll` (Windows HID system library) via
`LoadLibraryA("hid.dll")` inside `lookup_functions()`. If this fails on the Windows version
being used (e.g., function signatures changed), `hid_init()` returns -1, `hid_enumerate()`
returns NULL, and `hid_open()` returns NULL → 502. Modern Windows 10/11 may have renamed or
restructured these internal APIs.

**Test**: Write a small C or Python ctypes program that loads `hid.dll` directly and calls
`HidD_GetAttributes` to confirm these symbols are accessible.

### H5 — Exclusive Access by Another Process [LOW]

If another process (e.g., a previous crashed Python/LabView session, or the Windows HID service
subsystem) holds exclusive write access to the device, `CreateFile()` with `GENERIC_WRITE`
fails silently and `hid_open_path()` returns NULL.

**Test**: Reboot the lab PC and try immediately (before any other software touches the device).
Also check Device Manager for any "!" markers on the device.

### H6 — Driver Stack Mismatch (WinUSB vs HID Class) [LOW]

If someone previously used Zadig or similar to reassign the device from the Windows HID class
driver to WinUSB/libusb, the `SetupDiGetClassDevs` call in `hid.c` (which queries the HID class
GUID) would find nothing. Python's `hid` module, if installed with `pip install hidapi` on
Windows, may use libusb as a backend and thus still find the device under WinUSB.

**Test**: In Device Manager, find the spectrometer. Right-click → Properties → Driver tab. Note
the driver provider (Microsoft = HID class; libusb/WinUSB = custom). If it shows WinUSB, the
`hid.c` based HIDAPI cannot talk to it.

---

## Debug Steps (Run on Lab PC with Device Connected)

### Step 1 — Enumerate all HID devices and find the spectrometer

```python
import hid

print("All HID devices:")
for d in hid.enumerate(0, 0):
    if d['manufacturer_string'] or d['product_string']:
        print(f"  VID=0x{d['vendor_id']:04X}  PID=0x{d['product_id']:04X}"
              f"  Mfr={d['manufacturer_string']}  Prod={d['product_string']}")
```

**Goal**: Confirm the exact VID and PID of the ASQE spectrometer. Write them down.
Compare to `0xE220, 0x0100` (what the DLL expects) vs `0x20E2, 0x0001` (user's prior test).

---

### Step 2 — Check if `connectToDeviceByIndex` is exported from the DLL

The DLL likely exports this function (it's in `func_descr.txt`). Try calling it from Python:

```python
import ctypes, sys, os, platform

if sys.platform == 'win32':
    arch, _ = platform.architecture()
    lib_name = 'libspectr64bit.dll' if arch == '64bit' else 'libspectr.dll'
else:
    lib_name = 'libspectr.dylib' if sys.platform == 'darwin' else 'libspectr.so'

lib_path = os.path.join(os.path.dirname(__file__), 'lib', lib_name)
lib = ctypes.CDLL(lib_path)

# Try connectToDeviceByIndex
try:
    lib.connectToDeviceByIndex.argtypes = [ctypes.c_uint]
    lib.connectToDeviceByIndex.restype = ctypes.c_int
    result = lib.connectToDeviceByIndex(0)
    print(f"connectToDeviceByIndex(0) returned: {result}")
except AttributeError:
    print("connectToDeviceByIndex not found in this DLL")

# Try getDeviceCount
try:
    lib.getDeviceCount.argtypes = []
    lib.getDeviceCount.restype = ctypes.c_int
    count = lib.getDeviceCount()
    print(f"getDeviceCount() returned: {count}")
except AttributeError:
    print("getDeviceCount not found in this DLL")
```

**Goal**: If `connectToDeviceByIndex(0)` returns 0 (success), the DLL CAN talk to the device
and the root cause is specifically in how `connectToDevice(NULL)` resolves the VID/PID. This
also gives an immediate workaround — replace the `connect()` call in `libspec.py`.

---

### Step 3 — Check DLL dependencies on Windows

Open PowerShell on the lab PC and run:

```powershell
# For the Python DLL
dumpbin /dependents ".\lib\libspectr.dll"
dumpbin /dependents ".\lib\libspectr64bit.dll"

# For the LabView DLL
dumpbin /dependents "All software to download\LabView_Examples\hidapi_64bits.dll"
dumpbin /dependents "All software to download\EXE program\spectrlib_shared.dll"
```

(Use `x64 Native Tools Command Prompt for VS` or install binutils if `dumpbin` isn't available.)

Alternative using Python:
```python
import subprocess
result = subprocess.run(
    ['dumpbin', '/dependents', r'lib\libspectr64bit.dll'],
    capture_output=True, text=True
)
print(result.stdout)
```

**Goal**: Determine whether `libspectr.dll` / `libspectr64bit.dll` imports `hidapi.dll` as an
external dependency. If yes, check if `hidapi.dll` is present in `lib/` or on the system PATH.

---

### Step 4 — Inspect exported symbols of the DLL

```python
import ctypes.util, subprocess, sys

# On Windows, list DLL exports:
result = subprocess.run(
    ['dumpbin', '/exports', r'lib\libspectr64bit.dll'],
    capture_output=True, text=True, shell=True
)
print(result.stdout)
```

Or use Python `pefile` package:
```python
# pip install pefile
import pefile
pe = pefile.PE(r'lib\libspectr64bit.dll')
for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
    print(exp.name)
```

**Goal**: Confirm whether `connectToDeviceByIndex` and `getDeviceCount` are actually exported.
Also look for `hid_open`, `hid_init`, `hid_enumerate` — their presence/absence tells us whether
HIDAPI is statically linked in or expected as a separate DLL.

---

### Step 5 — Direct VID/PID probe through ctypes

If the actual VID/PID (from Step 1) differs from `0xE220, 0x0100`, we cannot directly patch the
compiled DLL. However, we can verify the theory by making the DLL's internal `hid_enumerate`
behave correctly: if HIDAPI is exported, try calling it directly with the correct VID/PID.

If `hid_open` is exported from the DLL:
```python
# Only if hid_open is an exported symbol
lib.hid_open.argtypes = [ctypes.c_ushort, ctypes.c_ushort, ctypes.c_void_p]
lib.hid_open.restype = ctypes.c_void_p
handle = lib.hid_open(0x20E2, 0x0001, None)  # use actual VID/PID
print(f"hid_open handle: {handle}")  # non-zero = success
```

This directly tests whether the DLL's HIDAPI implementation can open the device with the
correct VID/PID, bypassing the hardcoded constants in `connectToDevice`.

---

## Recommended Fix Path

### Immediate workaround: `connectToDeviceByIndex`

If Step 2 shows `connectToDeviceByIndex(0)` works, update `libspec.py`:

```python
def connect(self):
    # First try connectToDeviceByIndex (enumerates by position, bypasses VID/PID filter)
    try:
        self.lib.connectToDeviceByIndex.argtypes = [ctypes.c_uint]
        self.lib.connectToDeviceByIndex.restype = ctypes.c_int
        result = self.lib.connectToDeviceByIndex(0)
        if result == 0:
            return
    except AttributeError:
        pass  # Function not exported; fall through to standard method

    result = self.lib.connectToDevice(None)
    if result != 0:
        raise ConnectionError(f"Failed to connect to device. Error code: {result}")
```

### Medium-term fix: Pure Python `hid` implementation

Since Python's `hid` module already successfully opens the device, the cleanest fix is to
**bypass the native DLL entirely** for USB communication and re-implement the protocol in
Python using `hid`. The protocol is fully documented in `libspectr.c`:

- All packet structures, opcodes, and response codes are known
- USB packets are 64 bytes + 1 report ID byte = 65 bytes total
- All communication is interrupt transfer (HID write/read)

This removes the dependency on a platform-specific binary DLL and fixes the issue permanently.
The implementation would replace all `self.lib.*` calls in `libspec.py` with `hid.device`
operations. See `libspectr.c` for the exact packet format of each command.

Example for `connectToDevice` replacement:
```python
import hid

self._dev = hid.device()
self._dev.open(0x20E2, 0x0001)  # confirmed actual VID/PID from Step 1
self._dev.set_nonblocking(False)
```

### If VID/PID byte-swap is confirmed

The DLL source has `0xE220, 0x0100` but the device is at `0x20E2, 0x0001`. On Windows, the
DLL would need to be recompiled with the corrected constants. The corrected lines in
`libspectr.c` would be:

```c
// CORRECTED (if device VID/PID is confirmed as 0x20E2, 0x0001)
#define USBD_VID    0x20E2
#define USBD_PID    0x0001
```

Then rebuild using the vendor-provided build system (likely MSVC or MinGW). The LabView
examples include `hidapi_XX bits.dll` which is a separately compiled HIDAPI; whether it has
the same byte-swap error depends on how the LabView VIs call the functions (likely via
`connectToDeviceByIndex` which bypasses the VID/PID filter).

---

## Files of Interest

| File | Relevance |
|---|---|
| `lib/libspectr.dll` | 32-bit Windows DLL used by Python on 32-bit Python |
| `lib/libspectr64bit.dll` | 64-bit Windows DLL used by Python on 64-bit Python |
| `lib/libspectr.dylib` | macOS DLL (uses IOKit HIDAPI, not the `hid.c` source) |
| `All software to download/DLL_source_code/libspectr.c` | C source — hardcodes VID/PID at lines 4-5 |
| `All software to download/DLL_source_code/hid.c` | Windows-only HIDAPI implementation bundled in DLL |
| `All software to download/LabView_Examples/hidapi_64bits.dll` | Separate HIDAPI used by LabView |
| `All software to download/EXE program/spectrlib_shared.dll` | Shared DLL without embedded HIDAPI(?) |
| `All software to download/func_descr.txt` | Documents `connectToDeviceByIndex` and `getDeviceCount` |
| `libspec.py` line 89 | The failing call: `self.lib.connectToDevice(None)` |

---

## Summary Checklist for Agent

1. [ ] Run HID enumeration on lab PC; record exact VID/PID of spectrometer
2. [ ] Compare VID/PID to `0xE220, 0x0100` — confirm byte-swap hypothesis
3. [ ] Try `connectToDeviceByIndex(0)` from Python on lab PC
4. [ ] Run `dumpbin /dependents` on `lib/libspectr64bit.dll` — check for `hidapi.dll` dependency
5. [ ] Run `dumpbin /exports` (or `pefile`) on `lib/libspectr64bit.dll` — confirm exported symbols
6. [ ] If `connectToDeviceByIndex` works: patch `libspec.py` immediately
7. [ ] If VID/PID mismatch confirmed: plan pure-Python hid reimplementation OR DLL recompile
8. [ ] If neither: investigate driver stack (Device Manager), reboot, check exclusive access

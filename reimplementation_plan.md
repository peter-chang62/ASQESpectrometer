# Pure Python HID Reimplementation Plan for `libspec.py`

**Date:** 2026-04-13  
**Author:** Claude Code (based on `libspectr.c` source analysis and `diagnosis_results.md`)  
**Goal:** Replace all ctypes/DLL transport calls in `libspec.py` with direct USB HID packet I/O
using Python's `hid` module, fixing the VID/PID byte-swap bug permanently without needing a
compiler or modifying any binary.

---

## Table of Contents

1. [Context & Goals](#1-context--goals)
2. [Exact Scope of Change](#2-exact-scope-of-change)
3. [Dependencies](#3-dependencies)
4. [Full Protocol Reference](#4-full-protocol-reference)
   - 4.1 [Physical Frame Format](#41-physical-frame-format)
   - 4.2 [Constants](#42-constants)
   - 4.3 [Command & Reply Opcode Table](#43-command--reply-opcode-table)
   - 4.4 [Endianness Rules](#44-endianness-rules)
   - 4.5 [Per-Command Packet Layouts](#45-per-command-packet-layouts)
   - 4.6 [Multi-Packet Protocol: getFrame](#46-multi-packet-protocol-getframe)
   - 4.7 [Multi-Packet Protocol: readFlash](#47-multi-packet-protocol-readflash)
   - 4.8 [Multi-Packet Protocol: writeFlash](#48-multi-packet-protocol-writeflash)
5. [Internal State to Track](#5-internal-state-to-track)
6. [Cross-Platform HID Read Behavior](#6-cross-platform-hid-read-behavior)
7. [New Transport Layer Design](#7-new-transport-layer-design)
8. [Step-by-Step Implementation](#8-step-by-step-implementation)
9. [Bugs & Deviations Found in the C Source](#9-bugs--deviations-found-in-the-c-source)
10. [Error Handling Strategy](#10-error-handling-strategy)
11. [Testing Checklist](#11-testing-checklist)

---

## 1. Context & Goals

The existing `libspec.py` calls into `lib/libspectr64bit.dll` (Windows) via ctypes. That DLL
hardcodes `VID=0xE220, PID=0x0100` internally, but the physical device enumerates at
`VID=0x20E2, PID=0x0001` (byte-swapped). This causes `connectToDevice()` to always return 502.

The fix is to bypass the DLL entirely and talk to the device directly using Python's `hid`
module, which already opens the device successfully (`hid.device().open(0x20E2, 0x0001)`).

All public methods of `ASQESpectrometer` must remain unchanged so that `spectrum.py`,
`spectrum_norm.py`, and `spectrum_calib.py` continue to work without modification.

---

## 2. Exact Scope of Change

### Changes confined to `libspec.py` only

| Section | Before | After |
|---|---|---|
| Import | `import ctypes`, `import platform` | `import hid`, remove both ctypes imports |
| `__init__` | Loads DLL via ctypes, calls `_setup_function_prototypes()` | Opens `hid.device`, no DLL loading |
| `_setup_function_prototypes()` | Sets argtypes/restype for all DLL exports | **Deleted entirely** |
| `connect()` | `self.lib.connectToDevice(None)` | `self._dev.open(0x20E2, 0x0001)` |
| `configure_acquisition()` | ctypes calls to DLL | Packet-based calls via new helpers |
| `capture_frame()` | ctypes calls to DLL | Packet-based calls via new helpers |
| `read_flash()` | ctypes call to `self.lib.readFlash` | Packet-based implementation |
| `__del__` | `self.lib.disconnectDevice()` | `self._dev.close()` |
| **New** | — | `_write()`, `_read()`, `_write_read()` transport helpers |
| **New** | — | `_num_pixels_in_frame` instance variable (replaces C's `g_numOfPixelsInFrame`) |

### Preserved unchanged (no edits)

- `set_parameters()`
- `read_calibration_file()`
- `load_calibration_data()`
- `subtract_background()`
- `normalize_spectrum()`
- `get_calibrated_spectrum()`
- `get_spectrum()` (alias for `capture_frame()`)
- All constructor parameters and their defaults

---

## 3. Dependencies

```
hid >= 0.14.0   (already installed: hid==0.15.0 in conda env env38)
numpy           (already present)
```

`hid` is the `cython-hidapi` package (`pip install hid`). It wraps HIDAPI and works on
Windows (Win32 HID), Linux (hidraw), and macOS (IOKit).

**No other new dependencies.** Remove `ctypes` and `platform` from the import list.

---

## 4. Full Protocol Reference

All information derived directly from `All software to download/DLL_source_code/libspectr.c`
and `libspectr.h`.

### 4.1 Physical Frame Format

Every USB HID transaction is exactly **65 bytes** in one direction:

```
Byte 0:      Report ID = 0x00  (single-report device; always zero)
Byte 1:      Command/reply opcode
Bytes 2–64:  Command-specific payload (63 bytes available, padded with 0x00)
```

Total = 65 bytes (1 report ID + 64 payload = `EXTENDED_PACKET_SIZE`).

`hid_write` sends 65 bytes.  
`hid_read_timeout` requests 65 bytes but the device returns **64 bytes** on Windows/macOS
(the report ID is stripped from input reports by the Win32 HID driver). See §6 for
cross-platform handling.

### 4.2 Constants

Derived from `libspectr.c` preprocessor defines:

| Constant | Value | Meaning |
|---|---|---|
| `PACKET_SIZE` | 64 | USB HID payload size |
| `EXTENDED_PACKET_SIZE` | 65 | Includes Report ID byte |
| `MAX_PACKETS_IN_FRAME` | 124 | Max response packets per `getFrame` |
| `NUM_OF_PIXELS_IN_PACKET` | 30 | Pixels per `getFrame` response packet |
| `MAX_READ_FLASH_PACKETS` | 100 | Max packets per `readFlash` burst |
| `MAX_FLASH_WRITE_PAYLOAD` | 58 | Max data bytes per `writeFlash` packet |
| `FLASH_READ_PAYLOAD` | 60 | Data bytes per `readFlash` response packet (`PACKET_SIZE - 4`) |
| `REMAINING_PACKETS_ERROR` | 250 | Sentinel: packetsRemaining ≥ 250 → error |
| `STANDARD_TIMEOUT_MS` | 100 | Timeout for all commands except erase |
| `ERASE_FLASH_TIMEOUT_MS` | 5000 | Timeout for flash erase |
| `VID` | 0x20E2 | Actual device Vendor ID (corrected) |
| `PID` | 0x0001 | Actual device Product ID (corrected) |

### 4.3 Command & Reply Opcode Table

| Command | Request Byte | Reply Byte | Direction |
|---|---|---|---|
| `getStatus` | 0x01 | 0x81 | write + read |
| `setExposure` | 0x02 | 0x82 | write + read |
| `setAcquisitionParameters` | 0x03 | 0x83 | write + read |
| `setFrameFormat` | 0x04 | 0x84 | write + read |
| `setExternalTrigger` | 0x05 | 0x85 | write + read |
| `triggerAcquisition` | 0x06 | — | write-only |
| `clearMemory` | 0x07 | 0x87 | write + read |
| `getFrameFormat` | 0x08 | 0x88 | write + read |
| `getAcquisitionParameters` | 0x09 | 0x89 | write + read |
| `getFrame` | 0x0A | 0x8A | write + multi-read |
| `setOpticalTrigger` | 0x0B | 0x8B | write + read |
| `setAllParameters` | 0x0C | 0x8C | write + read |
| `readFlash` | 0x1A | 0x9A | write + multi-read (burst) |
| `writeFlash` | 0x1B | 0x9B | write + read per chunk |
| `eraseFlash` | 0x1C | 0x9C | write + read (5 s timeout) |
| `resetDevice` | 0xF1 | — | write-only |
| `detachDevice` | 0xF2 | — | write-only |

### 4.4 Endianness Rules

All multi-byte integers are **little-endian** throughout the protocol.

Packing in Python:
```python
import struct
# uint16 → 2 bytes LE
lo, hi = struct.pack('<H', value)
# uint32 → 4 bytes LE
b0, b1, b2, b3 = struct.pack('<I', value)
```

Unpacking from response buffer `data` (where `data[0]` is the reply opcode):
```python
value_16 = struct.unpack_from('<H', bytes(data), offset)[0]
value_32 = struct.unpack_from('<I', bytes(data), offset)[0]
```

### 4.5 Per-Command Packet Layouts

Each table shows the byte indices **in the 65-byte packet sent** and the **64-byte response
received** (after stripping the report ID — see §6). Index 0 of the sent packet is always the
Report ID (0x00); index 1 is the opcode. Index 0 of the received packet is the reply opcode.

---

#### `getStatus` — opcode 0x01 → reply 0x81

**Send** (only bytes 0–1 matter, rest are zero-padded):
```
[0] = 0x00  (report ID)
[1] = 0x01  (STATUS_REQUEST)
```

**Receive:**
```
[0] = 0x81
[1] = statusFlags
        bit 0: acquisition active (1 = running)
        bit 1: device memory full (must clearMemory() before restart)
[2] = LO(framesInMemory)
[3] = HI(framesInMemory)
```

Python decode:
```python
status_flags = data[1]
frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
acq_active = bool(status_flags & 0x01)
mem_full   = bool(status_flags & 0x02)
```

---

#### `setExposure` — opcode 0x02 → reply 0x82

**Send:**
```
[0] = 0x00
[1] = 0x02
[2..5] = timeOfExposure as uint32 LE  (units: 10 µs; e.g. 1000 = 10 ms)
[6] = force  (1 = apply immediately mid-acquisition, 0 = apply next trigger)
```

**Receive:**
```
[0] = 0x82
[1] = errorCode  (0 = OK)
```

---

#### `setAcquisitionParameters` — opcode 0x03 → reply 0x83

**Send:**
```
[0]    = 0x00
[1]    = 0x03
[2..3] = numOfScans as uint16 LE       (1–137)
[4..5] = numOfBlankScans as uint16 LE  (0 for scanMode=3)
[6]    = scanMode as uint8             (0=continuous, 1=first-frame-idle,
                                        2=every-frame-idle, 3=averaging)
[7..10]= timeOfExposure as uint32 LE
```

**Receive:**
```
[0] = 0x83
[1] = errorCode
```

---

#### `setFrameFormat` — opcode 0x04 → reply 0x84

**Send:**
```
[0]    = 0x00
[1]    = 0x04
[2..3] = numOfStartElement as uint16 LE  (0–3647)
[4..5] = numOfEndElement as uint16 LE    (0–3647, must be > start)
[6]    = reductionMode as uint8
           0 = none (1:1)
           1 = 2:1 binning
           2 = 4:1 binning
           3 = 8:1 binning
```

**Receive:**
```
[0]    = 0x84
[1]    = errorCode
[2..3] = numOfPixelsInFrame as uint16 LE
           = (endElement - startElement + 1) / reductionFactor
```

**Side effect:** store `numOfPixelsInFrame` as `self._num_pixels_in_frame`.

---

#### `getFrameFormat` — opcode 0x08 → reply 0x88

**Send:**
```
[0] = 0x00
[1] = 0x08
```

**Receive:**
```
[0]    = 0x88
[1..2] = numOfStartElement as uint16 LE
[3..4] = numOfEndElement as uint16 LE
[5]    = reductionMode as uint8
[6..7] = numOfPixelsInFrame as uint16 LE
```

**Side effect:** same as `setFrameFormat` — store `self._num_pixels_in_frame`.

---

#### `setExternalTrigger` — opcode 0x05 → reply 0x85

**Send:**
```
[0] = 0x00
[1] = 0x05
[2] = enableMode    (0=disabled, 1=enabled, 2=one-shot)
[3] = signalFrontMode  (0=disabled, 1=rising, 2=falling, 3=both)
```

**Receive:**
```
[0] = 0x85
[1] = errorCode
```

---

#### `triggerAcquisition` — opcode 0x06 (write-only, no reply)

**Send:**
```
[0] = 0x00
[1] = 0x06
```

No response is read. The C source calls `_writeOnlyFunction` which does not call
`hid_read_timeout` after the write.

---

#### `clearMemory` — opcode 0x07 → reply 0x87

**Send:**
```
[0] = 0x00
[1] = 0x07
```

**Receive:**
```
[0] = 0x87
[1] = errorCode
```

---

#### `getAcquisitionParameters` — opcode 0x09 → reply 0x89

**Send:**
```
[0] = 0x00
[1] = 0x09
```

**Receive:**
```
[0]     = 0x89
[1..2]  = numOfScans as uint16 LE
[3..4]  = numOfBlankScans as uint16 LE
[5]     = scanMode
[6..9]  = timeOfExposure as uint32 LE
```

---

#### `setOpticalTrigger` — opcode 0x0B → reply 0x8B

**Send:**
```
[0]    = 0x00
[1]    = 0x0B
[2]    = enableMode  (0=disabled, 1=threshold trigger in scanMode=0)
[3..4] = pixel as uint16 LE  (0–3647: which pixel to monitor)
[5..6] = threshold as uint16 LE  (ADC threshold value)
```

**Receive:**
```
[0] = 0x8B
[1] = errorCode
```

---

#### `setAllParameters` — opcode 0x0C → reply 0x8C

Combined `setAcquisitionParameters` + `setExternalTrigger` in one round-trip.

**Send:**
```
[0]     = 0x00
[1]     = 0x0C
[2..3]  = numOfScans as uint16 LE
[4..5]  = numOfBlankScans as uint16 LE
[6]     = scanMode
[7..10] = timeOfExposure as uint32 LE
[11]    = enableMode (external trigger)
[12]    = signalFrontMode (external trigger)
```

**Receive:**
```
[0] = 0x8C
[1] = errorCode
```

> ⚠️ **Bug in C source:** `setAllParameters` in `libspectr.c` (line 1022) checks for
> `CORRECT_GET_ACQUISITION_PARAMETERS_REPLY` (0x89) instead of
> `CORRECT_SET_ALL_PARAMETERS_REPLY` (0x8C). The Python reimplementation should check for
> **0x8C** (the correct constant). If the device actually replies with 0x89, adjust.

---

#### `eraseFlash` — opcode 0x1C → reply 0x9C (5 s timeout)

**Send:**
```
[0] = 0x00
[1] = 0x1C
```

**Receive (5000 ms timeout):**
```
[0] = 0x9C
[1] = errorCode
```

---

#### `resetDevice` — opcode 0xF1 (write-only)

**Send:**
```
[0] = 0x00
[1] = 0xF1
```

No response. Device reverts all parameters to factory defaults.
Factory defaults: `numOfScans=1`, `numOfBlankScans=0`, `scanMode=0`,
`exposureTime=10` (100 µs), full pixel range, `reductionMode=0`.

---

#### `detachDevice` — opcode 0xF2 (write-only)

**Send:**
```
[0] = 0x00
[1] = 0xF2
```

No response. Device soft-disconnects and re-enumerates from defaults.

---

### 4.6 Multi-Packet Protocol: getFrame

`getFrame` is the most complex command. It retrieves a captured frame from device RAM.

#### Frame index argument

- `numOfFrame`: 0-based index of frame in device memory
- `0xFFFF` (65535): special value for scan mode 3 (frame averaging) — retrieves the average

#### Computing packet count

```python
num_pixels = self._num_pixels_in_frame   # must be > 0 before calling
packets_needed = math.ceil(num_pixels / 30)   # 30 pixels per packet
if packets_needed > 124:
    raise RuntimeError(f"Frame too large: {packets_needed} packets > 124 max")
```

Default pixel range (0–3647): `num_pixels = 3648`, `packets_needed = ceil(3648/30) = 122`.

#### Request packet (one write, then read loop)

```
[0] = 0x00
[1] = 0x0A
[2] = 0x00  (pixelOffset LO — always 0 in request)
[3] = 0x00  (pixelOffset HI — always 0 in request)
[4..5] = numOfFrame as uint16 LE
[6] = packets_needed as uint8
```

#### Response packet format (one packet per 30 pixels)

```
[0]    = 0x8A  (CORRECT_GET_FRAME_REPLY)
[1..2] = pixelOffset as uint16 LE  — index of first pixel in this packet
[3]    = packetsRemaining
           First packet: packetsRemaining = packets_needed - 1
           Last packet:  packetsRemaining = 0
           Error if packetsRemaining >= 250
[4..63] = 30 × uint16 LE pixel values (60 bytes)
           pixels[pixelOffset .. pixelOffset+29]
```

#### Validation rule

After receiving packet `n` (1-indexed):
```python
expected_remaining = packets_needed - n
if data[3] >= 250 or data[3] != expected_remaining:
    raise RuntimeError("getFrame: packet count mismatch (error 507)")
```

#### Loop pseudocode

```python
buf = [0] * num_pixels
packets_received = 0

self._write(0x0A, [
    0x00, 0x00,                          # pixelOffset = 0
    *struct.pack('<H', num_frame),        # frame number
    packets_needed                        # packet count
])

while True:
    data = self._read(timeout_ms=100)
    if data[0] != 0x8A:
        raise RuntimeError(f"getFrame: wrong reply opcode 0x{data[0]:02X}")
    packets_received += 1
    packets_remaining = data[3]
    expected = packets_needed - packets_received
    if packets_remaining >= 250 or packets_remaining != expected:
        raise RuntimeError("getFrame: packet count mismatch")
    pixel_offset = struct.unpack_from('<H', bytes(data), 1)[0]
    for i in range(30):
        idx = pixel_offset + i
        if idx >= num_pixels:
            break
        buf[idx] = struct.unpack_from('<H', bytes(data), 4 + i*2)[0]
    if packets_remaining == 0:
        break

return buf
```

The output `buf` is a list of `num_pixels` uint16 values. The existing `capture_frame()` stores
this as a `(ctypes.c_uint16 * 3694)()` buffer; the reimplementation returns a numpy array or
a list; `subtract_background()` indexes into it as `data[32:3685]` etc., so returning a numpy
`uint16` array of length `num_pixels` (or 3694 padded) is sufficient.

---

### 4.7 Multi-Packet Protocol: readFlash

`readFlash` reads up to `bytesToRead` bytes from device flash starting at `absoluteOffset`.

#### Payload size per response packet

`payloadSize = PACKET_SIZE - 4 = 60` bytes (64 − 4 header bytes: opcode + localOffset[2] + packetsRemaining).

#### Burst limit

Max 100 packets per USB request burst = 6000 bytes per burst.
For `bytesToRead > 6000`, must send multiple burst requests.

> **Note:** The C source has a bug — `continueGetInReport` is never reset to `True` between
> bursts, so only the first 6000 bytes of any multi-burst read would succeed. The Python
> reimplementation must fix this (reset the flag at the start of each burst). In practice,
> `libspec.py` calls `read_flash(offset, 1000)` in 1000-byte chunks, so the bug is never
> triggered, but the Python code should be correct anyway.

#### Request packet (one per burst)

```
[0]    = 0x00
[1]    = 0x1A
[2..5] = (absoluteOffset + burst_base_offset) as uint32 LE
[6]    = packets_this_burst  (min(remaining_packets, 100))
```

#### Response packet format (one packet per 60 bytes of flash)

```
[0]    = 0x9A  (CORRECT_READ_FLASH_REPLY)
[1..2] = localOffset as uint16 LE  — byte offset within this burst (0, 60, 120, ...)
[3]    = packetsRemaining (within this burst, not total)
[4..63]= 60 bytes of flash data
```

#### Validation

Same pattern as getFrame:
```python
if data[3] >= 250 or data[3] != (packets_this_burst - packets_received_in_burst):
    raise RuntimeError("readFlash: packet count mismatch (error 510)")
```

#### Loop pseudocode

```python
payload_size = 60
total_packets = math.ceil(bytes_to_read / payload_size)
buf = bytearray(bytes_to_read)
total_received = 0
burst_offset = 0    # byte offset within buf for start of current burst

while total_packets > 0:
    burst_count = min(total_packets, 100)

    pkt = bytearray(65)
    pkt[0] = 0x00
    pkt[1] = 0x1A
    struct.pack_into('<I', pkt, 2, absolute_offset + burst_offset)
    pkt[6] = burst_count
    self._dev.write(list(pkt))

    for burst_pkt_idx in range(1, burst_count + 1):
        data = self._dev.read(65)      # see §6 for platform handling
        data = _strip_report_id(data)  # normalize to 64 bytes starting with opcode
        if data[0] != 0x9A:
            raise RuntimeError(f"readFlash: wrong reply 0x{data[0]:02X}")
        remaining = data[3]
        expected = burst_count - burst_pkt_idx
        if remaining >= 250 or remaining != expected:
            raise RuntimeError("readFlash: packet count mismatch")
        local_offset = struct.unpack_from('<H', bytes(data), 1)[0]
        for i in range(payload_size):
            buf_idx = burst_offset + local_offset + i
            if buf_idx >= bytes_to_read:
                break
            buf[buf_idx] = data[4 + i]
            total_received += 1

    total_packets -= burst_count
    burst_offset += burst_count * payload_size

return bytes(buf)
```

---

### 4.8 Multi-Packet Protocol: writeFlash

`writeFlash` writes `bytesToWrite` bytes into flash at `offset`. Flash can only be written to
erased (0xFF) cells. Must call `eraseFlash()` before overwriting.

Max 58 bytes per USB packet (`MAX_FLASH_WRITE_PAYLOAD`).

#### Per-chunk request packet

```
[0]    = 0x00
[1]    = 0x1B
[2..5] = current write offset as uint32 LE
[6]    = bytes_in_this_chunk  (min(remaining, 58))
[7..7+n-1] = data bytes for this chunk  (n = bytes_in_this_chunk)
```

#### Per-chunk response

```
[0] = 0x9B
[1] = errorCode  (0 = OK)
```

#### Loop structure

Iterate in 58-byte chunks:
```python
pos = 0
while pos < len(data_buf):
    chunk = data_buf[pos : pos + 58]
    n = len(chunk)
    pkt = bytearray(65)
    pkt[0] = 0x00
    pkt[1] = 0x1B
    struct.pack_into('<I', pkt, 2, offset + pos)
    pkt[6] = n
    pkt[7 : 7 + n] = chunk
    self._dev.write(list(pkt))
    reply = self._read(timeout_ms=100)
    if reply[0] != 0x9B:
        raise RuntimeError(f"writeFlash: wrong reply 0x{reply[0]:02X}")
    if reply[1] != 0:
        raise RuntimeError(f"writeFlash: device error {reply[1]}")
    pos += n
```

---

## 5. Internal State to Track

The C library maintains two module-global variables in the DLL. Their equivalents in Python:

| C global | Python attribute | Purpose |
|---|---|---|
| `hid_device* g_Device` | `self._dev` (a `hid.device` instance) | Open device handle |
| `uint16_t g_numOfPixelsInFrame` | `self._num_pixels_in_frame` (int) | Frame size, set by `setFrameFormat`/`getFrameFormat`, read by `getFrame` |
| `char* g_savedSerial` | Not needed | C uses this for reconnect; Python will simply re-open |

Initialize in `__init__`:
```python
self._dev = hid.device()
self._num_pixels_in_frame = 0
```

`_num_pixels_in_frame` starts at 0. If `getFrame` is called and it is 0, `getFrameFormat`
must be called first to populate it (mirrors the C code's behavior exactly).

---

## 6. Cross-Platform HID Read Behavior

The Python `hid` module's `device.read(n)` returns a list of bytes. The number of bytes
returned and whether the report ID byte is included depends on the OS:

| Platform | `dev.read(65)` returns | Byte 0 of result |
|---|---|---|
| Windows (Win32 HID) | 64 bytes | Reply opcode (report ID stripped by driver) |
| Linux (hidraw) | 65 bytes | 0x00 (report ID included) |
| macOS (IOKit) | 64 bytes | Reply opcode (report ID stripped) |

**Solution:** Write a normalization helper that always produces a 64-byte buffer where
`buf[0]` is the reply opcode, regardless of platform:

```python
def _normalize_response(self, raw: list[int]) -> list[int]:
    """Strip report ID byte if present, returning a 64-byte buffer."""
    if len(raw) == 65 and raw[0] == 0x00:
        return raw[1:]   # Linux: strip report ID
    if len(raw) == 64:
        return raw        # Windows/macOS: already stripped
    raise RuntimeError(f"Unexpected read length {len(raw)}")
```

This helper is called on every response. It is safe because:
- Valid reply opcodes are in the range 0x81–0x9C — none are 0x00
- So `raw[0] == 0x00` unambiguously identifies the report ID byte

---

## 7. New Transport Layer Design

### 7.1 Helper methods

Three internal helpers encapsulate all HID I/O:

#### `_write(opcode, payload=None)`

Builds a 65-byte packet and writes it.

```python
def _write(self, opcode: int, payload: list[int] | None = None) -> None:
    pkt = [0x00, opcode] + (payload or [])
    pkt += [0x00] * (65 - len(pkt))   # zero-pad to 65 bytes
    written = self._dev.write(pkt)
    if written != 65:
        raise RuntimeError(f"HID write failed: wrote {written} bytes (expected 65), error 504")
```

#### `_read(timeout_ms=100)`

Reads one 64-byte normalized response.

```python
def _read(self, timeout_ms: int = 100) -> list[int]:
    raw = self._dev.read(65, timeout_ms)
    if not raw:
        raise RuntimeError(f"HID read timeout after {timeout_ms} ms, error 505")
    return self._normalize_response(raw)
```

#### `_write_read(opcode, payload, expected_reply, timeout_ms=100)`

Convenience: write then read, validate reply opcode.

```python
def _write_read(self, opcode: int, payload: list[int] | None,
                expected_reply: int, timeout_ms: int = 100) -> list[int]:
    self._write(opcode, payload)
    data = self._read(timeout_ms)
    if data[0] != expected_reply:
        raise RuntimeError(
            f"Wrong reply opcode: expected 0x{expected_reply:02X}, got 0x{data[0]:02X} (error 506)"
        )
    return data
```

### 7.2 Connection methods

```python
def connect(self):
    self._dev = hid.device()
    self._dev.open(0x20E2, 0x0001)
    self._dev.set_nonblocking(False)   # blocking reads (mirrors C default)

def __del__(self):
    try:
        self._dev.close()
    except Exception:
        pass
```

### 7.3 Reworked `configure_acquisition()`

```python
def configure_acquisition(self):
    # setAcquisitionParameters
    payload = list(struct.pack('<H', self.num_of_scans))
    payload += list(struct.pack('<H', self.num_of_blank_scans))
    payload += [self.scan_mode]
    payload += list(struct.pack('<I', self.exposure_time))
    data = self._write_read(0x03, payload, 0x83)
    if data[1] != 0:
        raise RuntimeError(f"setAcquisitionParameters error code {data[1]}")

    # setFrameFormat
    payload = list(struct.pack('<H', self.num_of_start_element))
    payload += list(struct.pack('<H', self.num_of_end_element))
    payload += [self.reduction_mode]
    data = self._write_read(0x04, payload, 0x84)
    if data[1] != 0:
        raise RuntimeError(f"setFrameFormat error code {data[1]}")
    self._num_pixels_in_frame = struct.unpack_from('<H', bytes(data), 2)[0]
```

### 7.4 Reworked `capture_frame()`

```python
def capture_frame(self):
    # triggerAcquisition (write-only, no reply)
    self._write(0x06)

    # Poll getStatus until framesInMemory > 0
    while True:
        sleep(0.025)
        data = self._write_read(0x01, None, 0x81)
        frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
        if frames_in_memory > 0:
            break

    # getFrame (0xFFFF = retrieve average in scanMode=3; works for all modes)
    if self._num_pixels_in_frame == 0:
        self._get_frame_format()    # lazy-initialize pixel count

    return self._get_frame(0xFFFF)
```

### 7.5 Reworked `read_flash()`

```python
def read_flash(self, offset=0, size=1000):
    payload_size = 60
    total_packets = math.ceil(size / payload_size)
    buf = bytearray(size)
    total_received = 0
    burst_base = 0

    while total_packets > 0:
        burst_count = min(total_packets, 100)
        burst_payload = list(struct.pack('<I', offset + burst_base)) + [burst_count]
        self._write(0x1A, burst_payload)

        for burst_n in range(1, burst_count + 1):
            data = self._read(timeout_ms=100)
            if data[0] != 0x9A:
                raise RuntimeError(f"readFlash: wrong reply 0x{data[0]:02X} (error 505)")
            remaining = data[3]
            expected = burst_count - burst_n
            if remaining >= 250 or remaining != expected:
                raise RuntimeError("readFlash: packet count mismatch (error 510)")
            local_offset = struct.unpack_from('<H', bytes(data), 1)[0]
            for i in range(payload_size):
                buf_idx = burst_base + local_offset + i
                if buf_idx >= size:
                    break
                buf[buf_idx] = data[4 + i]
                total_received += 1

        total_packets -= burst_count
        burst_base += burst_count * payload_size

    return bytes(buf)
```

---

## 8. Step-by-Step Implementation

These are the exact edits to make to `libspec.py`, in order.

### Step 1 — Update imports

Remove: `import ctypes`, `import platform`  
Add: `import hid`, `import struct`, `import math`

```python
# Before:
import os
import ctypes
import sys
import numpy as np
from time import sleep
import platform

# After:
import os
import sys
import struct
import math
import hid
import numpy as np
from time import sleep
```

### Step 2 — Add `_num_pixels_in_frame` to `__init__`

In `__init__`, after the calibration variable block and before `_setup_function_prototypes()`:

```python
self._dev = hid.device()
self._num_pixels_in_frame = 0
```

### Step 3 — Remove DLL loading from `__init__`

Delete these lines from `__init__`:
```python
# DELETE the entire DLL loading block:
if sys.platform == 'win32':
    arch, _ = platform.architecture()
    if arch == '64bit':
        lib_name = 'libspectr64bit.dll'
    else:
        lib_name = 'libspectr.dll'
elif sys.platform == 'darwin':
    lib_name = 'libspectr.dylib'
else:
    lib_name = 'libspectr.so'
lib_path = os.path.join(os.path.dirname(__file__), 'lib', lib_name)
self.lib = ctypes.CDLL(lib_path)
```

### Step 4 — Delete `_setup_function_prototypes()`

Remove the entire method. Update `__init__` to remove the call to it.

### Step 5 — Replace `connect()`

```python
def connect(self):
    self._dev.open(0x20E2, 0x0001)
    self._dev.set_nonblocking(False)
```

### Step 6 — Add `_normalize_response()`, `_write()`, `_read()`, `_write_read()`

Insert these four methods between `connect()` and `read_flash()`. Full implementations as
shown in §7.1 and §6.

### Step 7 — Add `_get_frame_format()` helper

This mirrors `getFrameFormat` from the C source. Called lazily from `capture_frame()` if
`_num_pixels_in_frame` is 0.

```python
def _get_frame_format(self):
    data = self._write_read(0x08, None, 0x88)
    self._num_pixels_in_frame = struct.unpack_from('<H', bytes(data), 6)[0]
```

### Step 8 — Add `_get_frame()` helper

Full multi-packet implementation as described in §4.6.

```python
def _get_frame(self, num_frame: int) -> np.ndarray:
    import math
    num_pixels = self._num_pixels_in_frame
    packets_needed = math.ceil(num_pixels / 30)
    if packets_needed > 124:
        raise RuntimeError(f"Frame exceeds 124-packet maximum (error 508)")

    frame_payload = [0x00, 0x00]                       # pixelOffset = 0
    frame_payload += list(struct.pack('<H', num_frame)) # frame number
    frame_payload += [packets_needed]
    self._write(0x0A, frame_payload)

    buf = np.zeros(num_pixels, dtype=np.uint16)
    for n in range(1, packets_needed + 1):
        data = self._read(timeout_ms=100)
        if data[0] != 0x8A:
            raise RuntimeError(f"getFrame: wrong reply 0x{data[0]:02X} (error 506)")
        remaining = data[3]
        expected = packets_needed - n
        if remaining >= 250 or remaining != expected:
            raise RuntimeError("getFrame: packet count mismatch (error 507)")
        pixel_offset = struct.unpack_from('<H', bytes(data), 1)[0]
        for i in range(30):
            idx = pixel_offset + i
            if idx >= num_pixels:
                break
            buf[idx] = struct.unpack_from('<H', bytes(data), 4 + i * 2)[0]

    return buf
```

### Step 9 — Replace `configure_acquisition()`

Full implementation as shown in §7.3.

### Step 10 — Replace `capture_frame()`

Full implementation as shown in §7.4.

Note: The current code creates a `(ctypes.c_uint16 * 3694)()` buffer. After the rewrite,
`capture_frame()` returns a numpy `uint16` array of length `_num_pixels_in_frame`. The callers
(`subtract_background`, etc.) index into it as `data[32:3685]`; since default pixel range
gives 3648 pixels, indices 32–3684 are valid.

However, `subtract_background()` also accesses `data[3686:3692]`, which requires the buffer
to be at least 3693 elements long. The default `num_of_end_element = 3647` gives
`num_pixels = 3648`. Index 3692 > 3648 — **this would be an out-of-bounds access**.

Looking at this more carefully: the existing code uses `buffer_size = 3694` (a fixed
over-allocation). The indices `data[3686:3692]` and `data[15:31]` are accessing the
**overscan/dark pixels** that the CCD returns beyond the active 3648 pixels. The device
physically returns 3694 pixel values for the full range scan (the DLL always uses 3694 as the
buffer size regardless of pixel range setting).

**Resolution:** Always allocate a 3694-element buffer for `capture_frame()`, padding with
zeros beyond `_num_pixels_in_frame` if necessary. This preserves the existing indexing in
`subtract_background()` without any other changes.

Updated `_get_frame()` signature:
```python
def _get_frame(self, num_frame: int) -> np.ndarray:
    buf = np.zeros(3694, dtype=np.uint16)   # fixed size to preserve indexing
    num_pixels = self._num_pixels_in_frame
    ...
    # fill buf[0..num_pixels-1] as before
    return buf
```

### Step 11 — Replace `read_flash()`

Full implementation as shown in §7.5.

### Step 12 — Replace `__del__()`

```python
def __del__(self):
    try:
        self._dev.close()
    except Exception:
        pass
```

The `try/except` prevents errors if `__init__` failed before the device was opened.

---

## 9. Bugs & Deviations Found in the C Source

These are bugs in `libspectr.c` that the Python reimplementation should handle correctly.

| # | Location | Bug | Python handling |
|---|---|---|---|
| 1 | `libspectr.c` lines 4–5 | VID/PID byte-swapped (`0xE220/0x0100` instead of `0x20E2/0x0001`) | Root cause of the issue. Fixed by using the correct constants in `hid.open()`. |
| 2 | `setAllParameters` (line 1022) | Checks `CORRECT_GET_ACQUISITION_PARAMETERS_REPLY` (0x89) instead of `CORRECT_SET_ALL_PARAMETERS_REPLY` (0x8C) | Python should check 0x8C. If device actually replies 0x89, adjust. `setAllParameters` is not currently called by `libspec.py` so this is low priority. |
| 3 | `readFlash` burst loop | `continueGetInReport` never reset to `True` between bursts; only first 6000 bytes work for large reads | Python implementation resets the flag correctly at the start of each burst. |
| 4 | `libspec.py` line 33 (`spectrum_norm.py`) | Calls `get_calibrated_spectrum()` instead of `normalize_spectrum()` | Not in scope of this rewrite; noted in CLAUDE.md Known Issues. |

---

## 10. Error Handling Strategy

The C library uses return codes. The Python reimplementation uses exceptions.

| Condition | Exception |
|---|---|
| `hid_write` returned wrong byte count | `RuntimeError("HID write failed: error 504")` |
| `hid_read_timeout` returned empty (timeout) | `RuntimeError("HID read timeout: error 505")` |
| Wrong reply opcode in response | `RuntimeError("Wrong reply opcode: error 506")` |
| `getFrame` packet count mismatch | `RuntimeError("getFrame packet count mismatch: error 507")` |
| `getFrame` > 124 packets | `RuntimeError("Frame exceeds 124-packet maximum: error 508")` |
| `readFlash` packet count mismatch | `RuntimeError("readFlash packet count mismatch: error 510")` |
| Device error code (non-zero response byte 1) | `RuntimeError(f"Device error code {code}")` |
| `connect()` device not found | `hid.HIDException` (propagated from `hid.device.open()`) — wrap if needed |

The existing `libspec.py` only raises on `connect()` failures; all other DLL calls are
fire-and-forget. The new implementation adds explicit error checking at the transport level
for write and read operations. Device-level error codes (from response byte 1) should also be
checked for `setAcquisitionParameters`, `setFrameFormat`, and flash operations.

---

## 11. Testing Checklist

Execute the following tests in order on the lab PC with the device connected.

### Phase 1 — Connection

- [ ] `spec = ASQESpectrometer()` — no exception, no 502 error
- [ ] `spec._dev` is a valid `hid.device` (not None)
- [ ] `spec._num_pixels_in_frame` is 0 after `__init__` (before `configure_acquisition`)

### Phase 2 — configure_acquisition

- [ ] `spec.configure_acquisition()` returns without error
- [ ] `spec._num_pixels_in_frame` is 3648 (default full range, no reduction)
- [ ] Change `reduction_mode=1`, call `configure_acquisition()`, verify `_num_pixels_in_frame == 1824`

### Phase 3 — capture_frame

- [ ] `spec.configure_acquisition(); buf = spec.capture_frame()` returns without error
- [ ] `len(buf) == 3694`
- [ ] `buf.dtype == np.uint16`
- [ ] Values are non-zero (device is generating real data)

### Phase 4 — subtract_background / normalize

- [ ] `spec.subtract_background()` returns 3653-element float array
- [ ] No IndexError (verifies buf length is ≥ 3693)
- [ ] `spec.normalize_spectrum()` returns `(wavelength, intensity)` tuple
- [ ] `len(wavelength) == 3653`, `len(intensity) == 3653`

### Phase 5 — Calibrated spectrum

- [ ] `spec.get_calibrated_spectrum()` returns without error
- [ ] Wavelength range is approximately 242–952 nm

### Phase 6 — Flash read

- [ ] `spec.read_flash(offset=0, size=100)` returns 100 bytes
- [ ] `spec.read_calibration_file()` returns non-empty bytearray
- [ ] Calibration bytearray is valid UTF-8 and contains float values

### Phase 7 — Example scripts

- [ ] `python spectrum.py` — real-time plot appears and updates
- [ ] `python spectrum_norm.py` — normalized spectrum plot appears
- [ ] `python spectrum_calib.py` — calibrated spectrum in uW/cm²/nm appears

### Phase 8 — Cross-platform

- [ ] Run the same tests on macOS (using `libspectr.dylib` was the fallback; now irrelevant — hid works natively)
- [ ] Run the same tests on Linux if applicable

---

## Appendix: Packet Layout Quick Reference Card

```
SEND (65 bytes):  [0x00][opcode][payload... (up to 63 bytes)][0x00-padded]
RECV (64 bytes):  [reply_opcode][data bytes 1..63]

getStatus:        send [0x01]             recv [0x81][flags][LO_frames][HI_frames]
setAcqParams:     send [0x03][scansL][scansH][blanksL][blanksH][mode][t0][t1][t2][t3]
                  recv [0x83][errCode]
setFrameFormat:   send [0x04][startL][startH][endL][endH][reduce]
                  recv [0x84][errCode][pixelsL][pixelsH]
getFrameFormat:   send [0x08]
                  recv [0x88][startL][startH][endL][endH][reduce][pixelsL][pixelsH]
triggerAcq:       send [0x06]              (no reply)
getFrame:         send [0x0A][0x00][0x00][frameL][frameH][nPkts]
                  recv loop: [0x8A][offL][offH][pktsLeft][px0L][px0H]...[px29L][px29H]
readFlash:        send [0x1A][off0][off1][off2][off3][nPkts]
                  recv loop: [0x9A][locOffL][locOffH][pktsLeft][60 bytes data]
writeFlash:       send [0x1B][off0][off1][off2][off3][nBytes][data...]
                  recv: [0x9B][errCode]  (per chunk)
eraseFlash:       send [0x1C]             recv [0x9C][errCode]  (5 s timeout)
resetDevice:      send [0xF1]             (no reply)
detachDevice:     send [0xF2]             (no reply)
```

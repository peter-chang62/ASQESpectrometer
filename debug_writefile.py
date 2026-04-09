"""
Test using Windows CreateFile/WriteFile/ReadFile directly to bypass
cython-hidapi's output report size limit (which enforces HID descriptor).
"""
import ctypes
import ctypes.wintypes
import hid

USBD_VID = 0x20E2
USBD_PID = 0x0001

# --- Get device path from hid.enumerate ---
device_path = None
for d in hid.enumerate():
    if d['vendor_id'] == USBD_VID and d['product_id'] == USBD_PID:
        device_path = d['path']
        print(f"Device path: {device_path}")
        break

if device_path is None:
    print("Device not found!")
    exit(1)

# --- Open device with CreateFile (Windows API) ---
GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000

kernel32 = ctypes.windll.kernel32

handle = kernel32.CreateFileW(
    device_path.decode('utf-8') if isinstance(device_path, bytes) else device_path,
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE,
    None,
    OPEN_EXISTING,
    FILE_FLAG_OVERLAPPED,
    None
)
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
print(f"CreateFile handle: {handle}  (invalid={handle == INVALID_HANDLE_VALUE})")
if handle == INVALID_HANDLE_VALUE:
    print(f"  Last error: {kernel32.GetLastError()}")
    exit(1)

# --- Build the 65-byte setAcquisitionParameters packet ---
packet = bytes([0x00, 0x03, 0x01, 0x00, 0x00, 0x00, 0x03, 0xE8, 0x03, 0x00, 0x00] + [0]*54)
print(f"\nPacket len={len(packet)}: {[hex(b) for b in packet[:12]]} ...")

# --- Write with WriteFile ---
OVERLAPPED = ctypes.create_string_buffer(32)  # sizeof(OVERLAPPED) on 64-bit
bytes_written = ctypes.wintypes.DWORD(0)

success = kernel32.WriteFile(
    handle,
    packet,
    len(packet),
    ctypes.byref(bytes_written),
    ctypes.byref(ctypes.cast(OVERLAPPED, ctypes.POINTER(ctypes.c_byte)).contents) if False else None
)
err = kernel32.GetLastError()
print(f"WriteFile success={success}, bytes_written={bytes_written.value}, error={err}")

# For overlapped, use WriteFileEx or wait...
# Try non-overlapped: reopen without FILE_FLAG_OVERLAPPED
kernel32.CloseHandle(handle)

handle2 = kernel32.CreateFileW(
    device_path.decode('utf-8') if isinstance(device_path, bytes) else device_path,
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE,
    None,
    OPEN_EXISTING,
    0,  # no FILE_FLAG_OVERLAPPED
    None
)
print(f"\nNon-overlapped handle: {handle2}")
if handle2 == INVALID_HANDLE_VALUE:
    print(f"  Last error: {kernel32.GetLastError()}")
    exit(1)

bytes_written2 = ctypes.wintypes.DWORD(0)
success2 = kernel32.WriteFile(handle2, packet, len(packet), ctypes.byref(bytes_written2), None)
err2 = kernel32.GetLastError()
print(f"WriteFile(non-overlapped) success={success2}, bytes_written={bytes_written2.value}, error={err2}")

# --- Read response ---
buf = ctypes.create_string_buffer(64)
bytes_read = ctypes.wintypes.DWORD(0)
# Note: non-overlapped read on HID may block, add a short timeout via hid instead
kernel32.CloseHandle(handle2)

# Use hid for reading (since we just need to verify write worked)
dev = hid.device()
dev.open(USBD_VID, USBD_PID)
# Send via WriteFile using hid's internal handle? No, let's just re-test:
# Actually re-open non-overlapped and also read with ReadFile
dev.close()

print("\nDone.")

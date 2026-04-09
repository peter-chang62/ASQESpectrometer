"""
Test overlapped WriteFile with FILE_SHARE_READ only (exclusive write),
matching the exact hidapi v1 open pattern. Also test if 65-byte overlapped
write actually delivers 65 bytes to the device.
"""
import ctypes
import ctypes.wintypes
import hid
import time

USBD_VID = 0x20E2
USBD_PID = 0x0001

device_path = None
for d in hid.enumerate(USBD_VID, USBD_PID):
    device_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
    break

print(f"Device path: {device_path}")

kernel32 = ctypes.windll.kernel32

GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

# Match old hidapi exactly: GENERIC_READ|GENERIC_WRITE, FILE_SHARE_READ only, FILE_FLAG_OVERLAPPED
handle = kernel32.CreateFileW(
    device_path,
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ,   # NOT FILE_SHARE_WRITE - exclusive write like old hidapi
    None,
    OPEN_EXISTING,
    FILE_FLAG_OVERLAPPED,
    None
)
print(f"Handle: {handle}  invalid={handle == INVALID_HANDLE_VALUE}")
if handle == INVALID_HANDLE_VALUE:
    err = kernel32.GetLastError()
    print(f"Error: {err}")
    # Try with FILE_SHARE_WRITE too (cython-hidapi style)
    handle = kernel32.CreateFileW(
        device_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        None
    )
    print(f"Fallback handle (with FILE_SHARE_WRITE): {handle}")

if handle == INVALID_HANDLE_VALUE:
    print("Cannot open device!")
    exit(1)

# Create event for OVERLAPPED
class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_ulong),
        ("InternalHigh", ctypes.c_ulong),
        ("Offset", ctypes.c_ulong),
        ("OffsetHigh", ctypes.c_ulong),
        ("hEvent", ctypes.wintypes.HANDLE),
    ]

ol = OVERLAPPED()
ol.hEvent = kernel32.CreateEventW(None, True, False, None)

# STATUS command: 65 bytes, [0x00, 0x01, zeros...]
packet = bytes([0x00, 0x01] + [0]*63)
bytes_written = ctypes.wintypes.DWORD(0)

print(f"\nWriting 65 bytes via overlapped WriteFile...")
res = kernel32.WriteFile(
    handle,
    packet,
    len(packet),
    None,  # lpNumberOfBytesWritten = NULL for overlapped
    ctypes.byref(ol)
)
err = kernel32.GetLastError()
print(f"  WriteFile returned: {res}, GetLastError: {err} (997=IO_PENDING is expected)")

# Wait for completion
bw = ctypes.wintypes.DWORD(0)
res2 = kernel32.GetOverlappedResult(handle, ctypes.byref(ol), ctypes.byref(bw), True)
err2 = kernel32.GetLastError()
print(f"  GetOverlappedResult: res={res2}, bytes_written={bw.value}, error={err2}")
print(f"  Expected: bytes_written=65 (if all bytes sent) or 9 (if truncated)")

# Now read via cython-hidapi (which uses ReadFile internally)
time.sleep(0.1)
kernel32.CloseHandle(handle)

# Use hid to read the response
dev = hid.device()
dev.open(USBD_VID, USBD_PID)
dev.set_nonblocking(1)
resp = dev.read(64)
dev.close()
if resp:
    print(f"\nResponse after overlapped write: {[hex(b) for b in resp[:8]]}")
    print(f"  data[0]=0x{resp[0]:02X} (expected 0x81 for valid STATUS reply)")
else:
    print("\nNo response (timeout)")

kernel32.CloseHandle(ol.hEvent)
print("\nDone.")

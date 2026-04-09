"""
Replicate old hidapi: overlapped WriteFile with 65 bytes.
Fixed OVERLAPPED structure (ULONG_PTR = pointer-sized).
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

print(f"Device: {device_path}")

kernel32 = ctypes.windll.kernel32
GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
ERROR_IO_PENDING = 997

# Proper OVERLAPPED for 64-bit Windows
class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal",     ctypes.c_size_t),   # ULONG_PTR
        ("InternalHigh", ctypes.c_size_t),   # ULONG_PTR
        ("Offset",       ctypes.c_uint32),
        ("OffsetHigh",   ctypes.c_uint32),
        ("hEvent",       ctypes.c_void_p),
    ]

# Open device like old hidapi: FILE_SHARE_READ only, FILE_FLAG_OVERLAPPED
handle = kernel32.CreateFileW(
    device_path,
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ,
    None, OPEN_EXISTING,
    FILE_FLAG_OVERLAPPED,
    None
)
if handle == INVALID_HANDLE_VALUE:
    err = kernel32.GetLastError()
    print(f"CreateFileW(FILE_SHARE_READ) failed, error={err}. Trying with FILE_SHARE_WRITE...")
    handle = kernel32.CreateFileW(
        device_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | 0x2,
        None, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        None
    )
    if handle == INVALID_HANDLE_VALUE:
        print(f"All open attempts failed. Error: {kernel32.GetLastError()}")
        exit(1)

print(f"Opened handle: {handle}")

# Create manual-reset event
hEvent = kernel32.CreateEventW(None, True, False, None)
print(f"Event handle: {hEvent}")

# STATUS command (65 bytes)
packet = bytes([0x00, 0x01] + [0]*63)

for attempt in range(2):
    ol = OVERLAPPED()
    ol.hEvent = hEvent
    kernel32.ResetEvent(hEvent)

    print(f"\n--- Attempt {attempt+1}: WriteFile(65 bytes, overlapped) ---")
    bw_ptr = ctypes.c_uint32(0)
    res = kernel32.WriteFile(handle, packet, len(packet), None, ctypes.byref(ol))
    err = kernel32.GetLastError()
    print(f"  WriteFile res={res}, err={err} ({err}=IO_PENDING?)")

    if res == 0 and err == ERROR_IO_PENDING:
        # Wait for completion
        wait_res = kernel32.WaitForSingleObject(hEvent, 1000)  # 1s timeout
        print(f"  WaitForSingleObject: {wait_res} (0=WAIT_OBJECT_0)")

    bw = ctypes.c_uint32(0)
    res2 = kernel32.GetOverlappedResult(handle, ctypes.byref(ol), ctypes.byref(bw), False)
    err2 = kernel32.GetLastError()
    print(f"  GetOverlappedResult: res={res2}, bytes_written={bw.value}, err={err2}")
    print(f"  *** If bytes_written=65: ALL bytes sent; if 9: truncated ***")

    if attempt == 0:
        # Read response immediately after write
        time.sleep(0.15)
        kernel32.CloseHandle(handle)
        kernel32.CloseHandle(hEvent)

        dev = hid.device()
        dev.open(USBD_VID, USBD_PID)
        dev.set_nonblocking(1)
        resp = dev.read(64)
        dev.close()

        if resp:
            print(f"\nRead response: {[hex(b) for b in resp[:8]]}")
            if resp[0] == 0x81:
                print("*** VALID STATUS REPLY! Device working correctly!")
            else:
                print(f"  data[0]=0x{resp[0]:02X} (0x81 expected for STATUS)")
        else:
            print("\nNo response in 64-byte read.")
        break

print("\nDone.")

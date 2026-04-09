"""
Test feature reports and HidD_SetFeature as an alternative write path.
Also test if send_feature_report works in cython-hidapi.
"""
import hid
import ctypes
import ctypes.wintypes

USBD_VID = 0x20E2
USBD_PID = 0x0001

# --- Get device path ---
device_path = None
for d in hid.enumerate(USBD_VID, USBD_PID):
    device_path = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
    break

print(f"Device path: {device_path}")

# --- Test 1: cython-hidapi send_feature_report ---
dev = hid.device()
dev.open(USBD_VID, USBD_PID)

status_cmd = bytes([0x00, 0x01] + [0]*63)  # 65 bytes
print("\n--- Test: send_feature_report (STATUS cmd) ---")
try:
    r = dev.send_feature_report(status_cmd)
    print(f"  send_feature_report returned: {r}")
    resp = dev.read(64, timeout_ms=500)
    print(f"  read: len={len(resp)}, bytes={[hex(b) for b in resp[:6]]}")
except Exception as e:
    print(f"  send_feature_report failed: {e}")

dev.close()

# --- Test 2: HidD_SetFeature via ctypes ---
print("\n--- Test: HidD_SetFeature via ctypes ---")
hid_dll = ctypes.windll.LoadLibrary("hid.dll")

GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

kernel32 = ctypes.windll.kernel32
handle = kernel32.CreateFileW(
    device_path,
    GENERIC_READ | GENERIC_WRITE,
    FILE_SHARE_READ | FILE_SHARE_WRITE,
    None, OPEN_EXISTING, 0, None
)
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
print(f"  CreateFile handle: {handle}")

if handle != INVALID_HANDLE_VALUE:
    # Query MaxOutputReportLength to understand what Windows allows
    # Get preparsed data
    preparsed_ptr = ctypes.c_void_p()
    ok = hid_dll.HidD_GetPreparsedData(handle, ctypes.byref(preparsed_ptr))
    print(f"  HidD_GetPreparsedData: {ok}")
    if ok:
        # Get caps
        class HIDP_CAPS(ctypes.Structure):
            _fields_ = [
                ("Usage", ctypes.c_ushort),
                ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort),
            ]
        caps = HIDP_CAPS()
        HIDP_STATUS_SUCCESS = 0x00110000
        ntdll = ctypes.windll.LoadLibrary("ntdll.dll")
        hid_dll_path = ctypes.util.find_library("hid") if hasattr(ctypes, 'util') else "hid.dll"
        status = hid_dll.HidP_GetCaps(preparsed_ptr, ctypes.byref(caps))
        print(f"  HidP_GetCaps status: 0x{status:08X} (success=0x{HIDP_STATUS_SUCCESS:08X})")
        print(f"  InputReportByteLength  = {caps.InputReportByteLength}")
        print(f"  OutputReportByteLength = {caps.OutputReportByteLength}")
        print(f"  FeatureReportByteLength= {caps.FeatureReportByteLength}")
        hid_dll.HidD_FreePreparsedData(preparsed_ptr)

    # Try HidD_SetFeature with 65 bytes
    feat_buf = bytes([0x00, 0x01] + [0]*63)  # STATUS cmd as feature report
    ok2 = hid_dll.HidD_SetFeature(handle, feat_buf, len(feat_buf))
    err2 = kernel32.GetLastError()
    print(f"\n  HidD_SetFeature(65 bytes): returned={ok2}, error={err2}")

    # Try WriteFile with different sizes
    for size in [8, 9, 64, 65]:
        buf = bytes([0x00, 0x01] + [0]*(size-1))[:size]
        n = ctypes.wintypes.DWORD(0)
        ok3 = kernel32.WriteFile(handle, buf, size, ctypes.byref(n), None)
        err3 = kernel32.GetLastError()
        print(f"  WriteFile({size} bytes): success={ok3}, written={n.value}, error={err3}")

    kernel32.CloseHandle(handle)

print("\nDone.")

"""
Test if the DLL can actually communicate with the device on this system.
If DLL gets valid STATUS response (0x81), the DLL is sending data somehow.
If it gets 0x0D error too, the DLL is also broken.
"""
import ctypes
import os
import sys
import platform

# Load DLL
arch, _ = platform.architecture()
dll_name = 'libspectr64bit.dll' if arch == '64bit' else 'libspectr.dll'
lib_path = os.path.join(os.path.dirname(__file__), 'lib', dll_name)
print(f"Loading DLL: {lib_path} (exists={os.path.exists(lib_path)})")

lib = ctypes.CDLL(lib_path)

# Set up function signatures
lib.connectToDevice.argtypes = [ctypes.c_char_p]
lib.connectToDevice.restype = ctypes.c_int

lib.getStatus.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint16)]
lib.getStatus.restype = ctypes.c_int

lib.setAcquisitionParameters.argtypes = [
    ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint8, ctypes.c_uint32
]
lib.setAcquisitionParameters.restype = ctypes.c_int

lib.setFrameFormat.argtypes = [
    ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint8,
    ctypes.POINTER(ctypes.c_uint16)
]
lib.setFrameFormat.restype = ctypes.c_int

lib.disconnectDevice.argtypes = []
lib.disconnectDevice.restype = None

# Connect
print("\nConnecting to device...")
r = lib.connectToDevice(None)
print(f"  connectToDevice returned: {r}  (0=OK)")
if r != 0:
    print("  FAILED to connect!")
    sys.exit(1)

# Get status
print("\nGetting status...")
status = ctypes.c_uint8(0)
frames = ctypes.c_uint16(0)
r = lib.getStatus(ctypes.byref(status), ctypes.byref(frames))
print(f"  getStatus returned: {r}  (0=OK)")
print(f"  status={status.value}, frames={frames.value}")

# Set acquisition parameters
print("\nSetting acquisition parameters...")
r = lib.setAcquisitionParameters(
    ctypes.c_uint16(1),    # num_of_scans
    ctypes.c_uint16(0),    # num_of_blank_scans
    ctypes.c_uint8(3),     # scan_mode
    ctypes.c_uint32(1000)  # exposure_time
)
print(f"  setAcquisitionParameters returned: {r}  (0=OK)")

# Set frame format
print("\nSetting frame format...")
num_pixels = ctypes.c_uint16(0)
r = lib.setFrameFormat(
    ctypes.c_uint16(0),    # start element
    ctypes.c_uint16(3647), # end element
    ctypes.c_uint8(0),     # reduction_mode
    ctypes.byref(num_pixels)
)
print(f"  setFrameFormat returned: {r}  (0=OK)")
print(f"  num_pixels={num_pixels.value}")

lib.disconnectDevice()
print("\nDisconnected.")

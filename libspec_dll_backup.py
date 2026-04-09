import os
import ctypes
import sys
import numpy as np
from time import sleep
import platform

class ASQESpectrometer:
    def __init__(self):
        # Determine OS and load appropriate library
        if sys.platform == 'win32':
            # Get architecture
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

        # Initialize device parameters
        self.num_of_scans = 1
        self.num_of_blank_scans = 0
        self.exposure_time = 1000  # in microseconds (10 ms)
        self.scan_mode = 3
        self.num_of_start_element = 0
        self.num_of_end_element = 3647
        self.reduction_mode = 0

        # Initialize calibration variables
        self._calibration_data_loaded = False
        self.bck_aT = None
        self.wavelength = None
        self.norm_coef = None
        self.power_coef = None

        # Setup function prototypes
        self._setup_function_prototypes()

        # Connect to device
        self.connect()

    def _setup_function_prototypes(self):
        self.lib.connectToDevice.argtypes = [ctypes.c_char_p]
        self.lib.connectToDevice.restype = ctypes.c_int

        self.lib.disconnectDevice.argtypes = []
        self.lib.disconnectDevice.restype = None

        self.lib.setAcquisitionParameters.argtypes = [
            ctypes.c_uint16, ctypes.c_uint16,
            ctypes.c_uint8, ctypes.c_uint32
        ]
        self.lib.setAcquisitionParameters.restype = ctypes.c_int

        self.lib.setFrameFormat.argtypes = [
            ctypes.c_uint16, ctypes.c_uint16,
            ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint16)
        ]
        self.lib.setFrameFormat.restype = ctypes.c_int

        self.lib.getStatus.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint16)
        ]
        self.lib.getStatus.restype = ctypes.c_int

        self.lib.getFrame.argtypes = [
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_uint16
        ]
        self.lib.getFrame.restype = ctypes.c_int

        self.lib.triggerAcquisition.argtypes = []
        self.lib.triggerAcquisition.restype = ctypes.c_int

        self.lib.readFlash.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32
        ]
        self.lib.readFlash.restype = ctypes.c_int

    def connect(self):
        result = self.lib.connectToDevice(None)
        if result != 0:
            raise ConnectionError(f"Failed to connect to device. Error code: {result}")

    def read_flash(self, offset=0, size=1000):
        """
        Read bytes from the flash memory starting at the given offset.
        """
        READ_FLASH_OK = 0
        buffer = (ctypes.c_uint8 * size)()
        result = self.lib.readFlash(buffer, offset, size)
        if result != READ_FLASH_OK:
            raise RuntimeError(f"readFlesh failed with code {result}")
        return bytes(buffer)
    
    def read_calibration_file(self):
        offset = 0
        CHUNK_SIZE = 1000
        MAX_SIZE = 100000
        FOUND_TERMINATION = False
        full_data = bytearray()
        while offset <= MAX_SIZE and not FOUND_TERMINATION:
            data = self.read_flash(offset, CHUNK_SIZE)
            stop_index = data.find(b"\xff\xff")
            if stop_index != -1:
                full_data.extend(data[:stop_index])
                FOUND_TERMINATION = True
            else:
                full_data.extend(data)
            offset += CHUNK_SIZE
        return full_data
    
    def load_calibration_data(self):
        """Return calibration data, reading from flash only once."""
        # Return cached data if available
        if self._calibration_data_loaded:
            return
        
        # Read and parse flash data if not cached
        calib = self.read_calibration_file()
        decode_data = calib.decode("utf-8")
        lines = decode_data.splitlines()

        # Parse bck_aT from second line
        try:
            self.bck_aT = float(lines[1])
        except (ValueError, IndexError) as e:
            raise ValueError("Failed to parse bck_aT from calibration data") from e

        # Parse calibration arrays and convert to float
        self.wavelength = np.array(lines[12:3665], dtype=float)
        self.norm_coef = np.array(lines[3666:7319], dtype=float)
        self.power_coef = np.array(lines[7320:10973], dtype=float)
        self._calibration_data_loaded = True
    
    def set_parameters(self, num_of_scans=None, num_of_blank_scans=None, exposure_time=None,
                   scan_mode=None, num_of_start_element=None, num_of_end_element=None,
                   reduction_mode=None):
        if num_of_scans is not None:
            self.num_of_scans = num_of_scans
        if num_of_blank_scans is not None:
            self.num_of_blank_scans = num_of_blank_scans
        if exposure_time is not None:
            self.exposure_time = exposure_time
        if scan_mode is not None:
            self.scan_mode = scan_mode
        if num_of_start_element is not None:
            self.num_of_start_element = num_of_start_element
        if num_of_end_element is not None:
            self.num_of_end_element = num_of_end_element
        if reduction_mode is not None:
            self.reduction_mode = reduction_mode

    def configure_acquisition(self):
        self.lib.setAcquisitionParameters(
            ctypes.c_uint16(self.num_of_scans),
            ctypes.c_uint16(self.num_of_blank_scans),
            ctypes.c_uint8(self.scan_mode),
            ctypes.c_uint32(self.exposure_time)
        )

        num_pixels = ctypes.c_uint16(0)
        self.lib.setFrameFormat(
            ctypes.c_uint16(self.num_of_start_element),
            ctypes.c_uint16(self.num_of_end_element),
            ctypes.c_uint8(self.reduction_mode),
            ctypes.byref(num_pixels)
        )

    def capture_frame(self):
        self.lib.triggerAcquisition()

        status = ctypes.c_uint8(0)
        frames = ctypes.c_uint16(0)
        while frames.value == 0:
            sleep(0.025)
            self.lib.getStatus(ctypes.byref(status), ctypes.byref(frames))

        buffer_size = 3694
        buffer = (ctypes.c_uint16 * buffer_size)()
        self.lib.getFrame(buffer, 65535)

        return buffer

    def get_spectrum(self):
        return self.capture_frame()
    
    def subtract_background(self):
        """
        1. get_spectrum()
        2. Subtract background average from both ends of the array. Keeps only elements from index 32 to 3685.
        """
        data = self.capture_frame()
        devd = np.mean(data[15:31])      # average of elements 15 to 31
        devd2 = np.mean(data[3686:3692]) # average of elements 3686 to 3692
        background = (devd + devd2) / 2

        # Subtract background and slice
        corrected = data[32:3685] - background
        return corrected
    
    def normalize_spectrum(self):
        """
        1. subtract_background()
        2. Apply narmalization to spectrum: Normalization: spectrum[i] /= norm_coef[i]
        """
        if not self._calibration_data_loaded:
            self.load_calibration_data()
        
        data = self.subtract_background()
        # Convert to float for precision during calculations
        data = data.astype(np.float64)

        # Apply normalization coefficients
        data /= self.norm_coef
        return self.wavelength, data
    
    def get_calibrated_spectrum(self):
        """
        Apply full calibration to spectrum:
        1. normalize_spectrum()
        2. power calibration: spectrum[i] *= power_coef[i] / ((exposure_time) * bck_aT)
        """
        wavelength, data = self.normalize_spectrum()
        data *= self.power_coef / (self.exposure_time * self.bck_aT)
        return wavelength, data

    def __del__(self):
        self.lib.disconnectDevice()

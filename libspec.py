import math
import hid
import numpy as np
from time import sleep

USBD_VID = 0x20E2
USBD_PID = 0x0001

PACKET_SIZE = 64
EXTENDED_PACKET_SIZE = 65  # 1 report ID byte + 64 data bytes

STANDARD_TIMEOUT_MS = 100
ERASE_FLASH_TIMEOUT_MS = 5000

NUM_OF_PIXELS_IN_PACKET = 30
MAX_PACKETS_IN_FRAME = 124
REMAINING_PACKETS_ERROR = 250
MAX_READ_FLASH_PACKETS = 100
FLASH_PAYLOAD_SIZE = 60  # PACKET_SIZE - 4


class ASQESpectrometer:
    def __init__(self):
        # Device handle
        self._device = None
        self._num_pixels_in_frame = 0

        # Acquisition parameters
        self.num_of_scans = 1
        self.num_of_blank_scans = 0
        self.exposure_time = 1000  # multiples of 10 µs → 10 ms
        self.scan_mode = 3
        self.num_of_start_element = 0
        self.num_of_end_element = 3647
        self.reduction_mode = 0

        # Calibration cache
        self._calibration_data_loaded = False
        self.bck_aT = None
        self.wavelength = None
        self.norm_coef = None
        self.power_coef = None

        self.connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, serial=None):
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = hid.device()
        try:
            self._device.open(USBD_VID, USBD_PID, serial)
        except OSError as e:
            raise ConnectionError(f"Failed to connect to device: {e}") from e

    # ------------------------------------------------------------------
    # Low-level HID helpers
    # ------------------------------------------------------------------

    def _write(self, report: list):
        """Write a 65-byte HID report (report ID byte + 64 data bytes)."""
        packet = [0] * EXTENDED_PACKET_SIZE
        for i, b in enumerate(report):
            if i >= EXTENDED_PACKET_SIZE:
                break
            packet[i] = b & 0xFF
        result = self._device.write(packet)
        if result < 0:
            raise RuntimeError("HID write failed")

    def _read(self, timeout_ms=STANDARD_TIMEOUT_MS) -> list:
        """Read a 64-byte HID response (no report ID prefix in Python hid)."""
        data = self._device.read(PACKET_SIZE, timeout_ms=timeout_ms)
        if not data:
            raise RuntimeError("HID read timed out or returned empty response")
        return data

    def _write_read(self, report: list, expected_reply: int, timeout_ms=STANDARD_TIMEOUT_MS) -> list:
        """Write a command and read the response, validating the reply byte."""
        self._write(report)
        data = self._read(timeout_ms=timeout_ms)
        if data[0] != expected_reply:
            raise RuntimeError(
                f"Unexpected HID reply: got 0x{data[0]:02X}, expected 0x{expected_reply:02X}"
            )
        return data

    # ------------------------------------------------------------------
    # Device commands
    # ------------------------------------------------------------------

    def _set_acquisition_parameters(self):
        e = self.exposure_time
        report = [
            0x00, 0x03,
            self.num_of_scans & 0xFF, (self.num_of_scans >> 8) & 0xFF,
            self.num_of_blank_scans & 0xFF, (self.num_of_blank_scans >> 8) & 0xFF,
            self.scan_mode & 0xFF,
            e & 0xFF, (e >> 8) & 0xFF, (e >> 16) & 0xFF, (e >> 24) & 0xFF,
        ]
        data = self._write_read(report, 0x83)
        if data[1] != 0:
            raise RuntimeError(f"setAcquisitionParameters error code: {data[1]}")

    def _set_frame_format(self):
        s = self.num_of_start_element
        e = self.num_of_end_element
        report = [
            0x00, 0x04,
            s & 0xFF, (s >> 8) & 0xFF,
            e & 0xFF, (e >> 8) & 0xFF,
            self.reduction_mode & 0xFF,
        ]
        data = self._write_read(report, 0x84)
        if data[1] != 0:
            raise RuntimeError(f"setFrameFormat error code: {data[1]}")
        self._num_pixels_in_frame = data[2] | (data[3] << 8)

    def _trigger_acquisition(self):
        """Software trigger — write-only, no response expected."""
        self._write([0x00, 0x06])

    def _get_status(self):
        """Returns (status_flags, frames_in_memory)."""
        data = self._write_read([0x00, 0x01], 0x81)
        status_flags = data[1]
        frames_in_memory = data[2] | (data[3] << 8)
        return status_flags, frames_in_memory

    def _get_frame(self, frame_num=0xFFFF) -> np.ndarray:
        """Fetch a frame from device memory. frame_num=0xFFFF → averaged frame."""
        if self._num_pixels_in_frame == 0:
            # Query device for current frame format
            data = self._write_read([0x00, 0x08], 0x88)
            self._num_pixels_in_frame = data[6] | (data[7] << 8)

        num_packets = math.ceil(self._num_pixels_in_frame / NUM_OF_PIXELS_IN_PACKET)
        if num_packets > MAX_PACKETS_IN_FRAME:
            raise RuntimeError(f"Too many packets required: {num_packets}")

        report = [
            0x00, 0x0A,
            0x00, 0x00,  # pixel offset (always 0 for full frame)
            frame_num & 0xFF, (frame_num >> 8) & 0xFF,
            num_packets & 0xFF,
        ]
        self._write(report)

        buffer = np.zeros(3694, dtype=np.uint16)
        packets_received = 0

        while True:
            data = self._read()
            if data[0] != 0x8A:
                raise RuntimeError(f"Unexpected getFrame reply: 0x{data[0]:02X}")

            packets_received += 1
            packets_remaining = data[3]

            if packets_remaining >= REMAINING_PACKETS_ERROR:
                raise RuntimeError(f"getFrame packet error: remaining={packets_remaining}")
            if packets_remaining != num_packets - packets_received:
                raise RuntimeError("getFrame packet count mismatch")

            pixel_offset = data[1] | (data[2] << 8)
            for i in range(NUM_OF_PIXELS_IN_PACKET):
                idx = pixel_offset + i
                if idx >= self._num_pixels_in_frame:
                    break
                lo = data[4 + i * 2]
                hi = data[4 + i * 2 + 1]
                buffer[idx] = lo | (hi << 8)

            if packets_remaining == 0:
                break

        return buffer

    # ------------------------------------------------------------------
    # Flash
    # ------------------------------------------------------------------

    def read_flash(self, offset=0, size=1000) -> bytes:
        """Read bytes from flash memory starting at the given offset."""
        result = bytearray(size)
        bytes_received = 0
        current_offset = offset

        bytes_remaining = size
        while bytes_remaining > 0:
            batch_bytes = min(bytes_remaining, MAX_READ_FLASH_PACKETS * FLASH_PAYLOAD_SIZE)
            num_packets = math.ceil(batch_bytes / FLASH_PAYLOAD_SIZE)

            addr = current_offset
            report = [
                0x00, 0x1A,
                addr & 0xFF, (addr >> 8) & 0xFF,
                (addr >> 16) & 0xFF, (addr >> 24) & 0xFF,
                num_packets & 0xFF,
            ]
            self._write(report)

            packets_received = 0
            while True:
                data = self._read()
                if data[0] != 0x9A:
                    raise RuntimeError(f"Unexpected readFlash reply: 0x{data[0]:02X}")

                packets_received += 1
                packets_remaining = data[3]

                if packets_remaining >= REMAINING_PACKETS_ERROR:
                    raise RuntimeError(f"readFlash packet error: remaining={packets_remaining}")
                if packets_remaining != num_packets - packets_received:
                    raise RuntimeError("readFlash packet count mismatch")

                local_offset = data[1] | (data[2] << 8)
                for i in range(FLASH_PAYLOAD_SIZE):
                    dest = bytes_received + local_offset + i
                    if dest >= size:
                        break
                    result[dest] = data[4 + i]

                if packets_remaining == 0:
                    break

            batch_actual = num_packets * FLASH_PAYLOAD_SIZE
            bytes_received += batch_actual
            current_offset += batch_actual
            bytes_remaining -= batch_actual

        return bytes(result)

    def read_calibration_file(self) -> bytearray:
        offset = 0
        CHUNK_SIZE = 1000
        MAX_SIZE = 100000
        full_data = bytearray()
        while offset <= MAX_SIZE:
            data = self.read_flash(offset, CHUNK_SIZE)
            stop_index = data.find(b"\xff\xff")
            if stop_index != -1:
                full_data.extend(data[:stop_index])
                break
            full_data.extend(data)
            offset += CHUNK_SIZE
        return full_data

    def load_calibration_data(self):
        """Load calibration from flash, caching after first read."""
        if self._calibration_data_loaded:
            return

        calib = self.read_calibration_file()
        lines = calib.decode("utf-8").splitlines()

        try:
            self.bck_aT = float(lines[1])
        except (ValueError, IndexError) as e:
            raise ValueError("Failed to parse bck_aT from calibration data") from e

        self.wavelength = np.array(lines[12:3665], dtype=float)
        self.norm_coef = np.array(lines[3666:7319], dtype=float)
        self.power_coef = np.array(lines[7320:10973], dtype=float)
        self._calibration_data_loaded = True

    # ------------------------------------------------------------------
    # Acquisition pipeline
    # ------------------------------------------------------------------

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
        self._set_acquisition_parameters()
        self._set_frame_format()

    def capture_frame(self) -> np.ndarray:
        self.configure_acquisition()
        self._trigger_acquisition()

        while True:
            sleep(0.025)
            _, frames = self._get_status()
            if frames > 0:
                break

        return self._get_frame(0xFFFF)

    def get_spectrum(self):
        return self.capture_frame()

    def subtract_background(self) -> np.ndarray:
        """Capture frame and subtract averaged edge-pixel background."""
        data = self.capture_frame()
        devd = np.mean(data[15:31])
        devd2 = np.mean(data[3686:3692])
        background = (devd + devd2) / 2
        return data[32:3685] - background

    def normalize_spectrum(self):
        """Return (wavelengths, intensity) with normalization applied."""
        if not self._calibration_data_loaded:
            self.load_calibration_data()
        data = self.subtract_background().astype(np.float64)
        data /= self.norm_coef
        return self.wavelength, data

    def get_calibrated_spectrum(self):
        """Return (wavelengths, power) in uW/cm²/nm."""
        wavelength, data = self.normalize_spectrum()
        data *= self.power_coef / (self.exposure_time * self.bck_aT)
        return wavelength, data

    def __del__(self):
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass

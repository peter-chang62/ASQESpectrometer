import os
import sys
import struct
import math
import hid
import numpy as np
from time import sleep


class ASQESpectrometer:
    def __init__(self):
        # Initialize device parameters
        self.num_of_scans = 1
        self.num_of_blank_scans = 0
        self.exposure_time = 1000  # units: 10 µs (= 10 ms)
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

        # HID device handle and frame pixel count (set by configure_acquisition)
        self._dev = hid.device()
        self._num_pixels_in_frame = 0

        # Connect to device
        self.connect()

    def connect(self):
        self._dev.open(0x20E2, 0x0001)
        self._dev.set_nonblocking(False)

    def reset_device(self):
        """Send resetDevice (0xF1) — write-only, no reply.

        Reverts all acquisition parameters to factory defaults:
          numOfScans=1, numOfBlankScans=0, scanMode=0,
          exposureTime=10 (100 µs), full pixel range, reductionMode=0.

        Caller must sleep ≥1 s before issuing further commands.
        """
        self._write(0xF1)
        self._num_pixels_in_frame = 0   # pixel count unknown after reset

    def get_status(self):
        """Query device status.

        Returns (acq_active: bool, mem_full: bool, frames_in_memory: int).
        Raises RuntimeError on bad reply opcode (error 506) or timeout (505).
        """
        data = self._write_read(0x01, None, 0x01)
        status_flags     = data[1]
        frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
        acq_active = bool(status_flags & 0x01)
        mem_full   = bool(status_flags & 0x02)
        return acq_active, mem_full, frames_in_memory

    def clear_memory(self):
        """Clear all captured frames from device RAM (opcode 0x07 → 0x87).

        Required when statusFlags bit 1 (mem_full) is set before starting a new
        acquisition.  No-op if memory is already empty.
        """
        data = self._write_read(0x07, None, 0x07)
        if data[1] != 0:
            raise RuntimeError(f"clearMemory returned error code {data[1]}")

    # ── Transport helpers ──────────────────────────────────────────────────────

    def _normalize_response(self, raw):
        """Strip platform-specific report-ID prefix so the buffer starts with the cmd echo.

        Linux (hidraw): 65 bytes returned, byte[0] = 0x00 (report ID) → strip to 64 bytes.
        Windows:        64 bytes returned, byte[0] = 0x0D (report ID) → strip to 63 bytes.
        Reply opcode (cmd echo) is always byte[0] after stripping.
        """
        if len(raw) == 65 and raw[0] == 0x00:
            return raw[1:]   # Linux: strip 0x00 report ID → 64 bytes
        if len(raw) == 64 and raw[0] == 0x0D:
            return raw[1:]   # Windows: strip 0x0D report ID → 63 bytes
        return raw            # fallback

    def _write(self, opcode, payload=None):
        pkt = [0x00, opcode] + (payload or [])
        pkt += [0x00] * (65 - len(pkt))   # zero-pad to exactly 65 bytes
        written = self._dev.write(pkt)
        if written < 0:
            raise RuntimeError("HID write failed (error 504)")

    def _read(self, timeout_ms=100):
        raw = self._dev.read(65, timeout_ms)
        if not raw:
            raise RuntimeError(f"HID read timeout after {timeout_ms} ms (error 505)")
        return self._normalize_response(raw)

    def _write_read(self, opcode, payload, expected_reply, timeout_ms=100):
        self._write(opcode, payload)
        data = self._read(timeout_ms)
        if data[0] != expected_reply:
            raise RuntimeError(
                f"Wrong reply opcode: expected 0x{expected_reply:02X}, "
                f"got 0x{data[0]:02X} (error 506)"
            )
        return data

    # ── Frame helpers ──────────────────────────────────────────────────────────

    def _get_frame_format(self):
        """Query the device for current pixel count and cache it."""
        data = self._write_read(0x08, None, 0x08)
        self._num_pixels_in_frame = struct.unpack_from('<H', bytes(data), 6)[0]

    def _get_frame(self, num_frame):
        """Retrieve one captured frame from device RAM.

        Returns a 3694-element numpy uint16 array (fixed size so that
        subtract_background()'s hardcoded indices [15:31], [32:3685], [3686:3692]
        remain valid; elements beyond _num_pixels_in_frame are zero-padded).
        """
        num_pixels = self._num_pixels_in_frame
        packets_needed = math.ceil(num_pixels / 30)
        if packets_needed > 124:
            raise RuntimeError(f"Frame exceeds 124-packet maximum (error 508)")

        frame_payload = [0x00, 0x00]                         # pixelOffset = 0
        frame_payload += list(struct.pack('<H', num_frame))  # frame index
        frame_payload += [packets_needed]
        self._write(0x0A, frame_payload)

        buf = np.zeros(3694, dtype=np.uint16)
        for n in range(1, packets_needed + 1):
            data = self._read(timeout_ms=100)
            if data[0] != 0x0A:
                raise RuntimeError(
                    f"getFrame: wrong reply 0x{data[0]:02X} (error 506)"
                )
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

    # ── Flash I/O ──────────────────────────────────────────────────────────────

    def read_flash(self, offset=0, size=1000):
        """Read bytes from flash memory starting at the given offset."""
        payload_size = 60   # FLASH_READ_PAYLOAD = PACKET_SIZE - 4
        total_packets = math.ceil(size / payload_size)
        buf = bytearray(size)
        burst_base = 0

        while total_packets > 0:
            burst_count = min(total_packets, 100)   # MAX_READ_FLASH_PACKETS = 100
            burst_payload = list(struct.pack('<I', offset + burst_base)) + [burst_count]
            self._write(0x1A, burst_payload)

            for burst_n in range(1, burst_count + 1):
                data = self._read(timeout_ms=100)
                if data[0] != 0x1A:
                    raise RuntimeError(
                        f"readFlash: wrong reply 0x{data[0]:02X} (error 505)"
                    )
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

            total_packets -= burst_count
            burst_base += burst_count * payload_size

        return bytes(buf)

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
        if self._calibration_data_loaded:
            return

        calib = self.read_calibration_file()
        decode_data = calib.decode("utf-8")
        lines = decode_data.splitlines()

        try:
            self.bck_aT = float(lines[1])
        except (ValueError, IndexError) as e:
            raise ValueError("Failed to parse bck_aT from calibration data") from e

        self.wavelength = np.array(lines[12:3665], dtype=float)
        self.norm_coef = np.array(lines[3666:7319], dtype=float)
        self.power_coef = np.array(lines[7320:10973], dtype=float)
        self._calibration_data_loaded = True

    # ── Parameter control ──────────────────────────────────────────────────────

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
        payload = list(struct.pack('<H', self.num_of_scans))
        payload += list(struct.pack('<H', self.num_of_blank_scans))
        payload += [self.scan_mode]
        payload += list(struct.pack('<I', self.exposure_time))
        data = self._write_read(0x03, payload, 0x03)
        if data[1] != 0:
            raise RuntimeError(f"setAcquisitionParameters error code {data[1]}")

        payload = list(struct.pack('<H', self.num_of_start_element))
        payload += list(struct.pack('<H', self.num_of_end_element))
        payload += [self.reduction_mode]
        data = self._write_read(0x04, payload, 0x04)
        if data[1] != 0:
            raise RuntimeError(f"setFrameFormat error code {data[1]}")
        self._num_pixels_in_frame = struct.unpack_from('<H', bytes(data), 2)[0]

    # ── Acquisition ────────────────────────────────────────────────────────────

    def capture_frame(self, timeout_s=10):
        self._write(0x06)   # triggerAcquisition — write-only, no reply

        max_polls = int(timeout_s / 0.025)
        for _ in range(max_polls):
            sleep(0.025)
            data = self._write_read(0x01, None, 0x01)
            frames_in_memory = struct.unpack_from('<H', bytes(data), 2)[0]
            if frames_in_memory > 0:
                break
        else:
            raise RuntimeError(f"capture_frame: no frame ready after {timeout_s} s (error 505)")

        if self._num_pixels_in_frame == 0:
            self._get_frame_format()   # lazy init if configure_acquisition() was skipped

        return self._get_frame(0xFFFF)

    def get_spectrum(self):
        return self.capture_frame()

    # ── Signal processing ──────────────────────────────────────────────────────

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
        2. Apply normalization to spectrum: Normalization: spectrum[i] /= norm_coef[i]
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
        try:
            self._dev.close()
        except Exception:
            pass

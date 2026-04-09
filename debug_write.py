"""Test write with bytes vs list, check hidapi behavior."""
import hid

USBD_VID = 0x20E2
USBD_PID = 0x0001

dev = hid.device()
dev.open(USBD_VID, USBD_PID)

# Test 1: write as bytes
packet_bytes = bytes([0x00, 0x03, 0x01, 0x00, 0x00, 0x00, 0x03, 0xE8, 0x03, 0x00, 0x00] + [0]*54)
print(f"packet type=bytes, len={len(packet_bytes)}")
r = dev.write(packet_bytes)
print(f"write(bytes 65) returned: {r}")
data = dev.read(64, timeout_ms=500)
print(f"read: len={len(data)}, first4={[hex(b) for b in data[:4]]}")

dev.close()

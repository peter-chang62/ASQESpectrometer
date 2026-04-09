"""
Controlled protocol test:
1. Check what device sends spontaneously (no write)
2. Flush aggressively, write STATUS, wait, read multiple packets
3. Test if any valid responses come back
"""
import hid
import time

USBD_VID = 0x20E2
USBD_PID = 0x0001

dev = hid.device()
dev.open(USBD_VID, USBD_PID)

def flush_all(dev, timeout_ms=200, label="flush"):
    """Drain all pending packets with a long timeout."""
    dev.set_nonblocking(0)
    count = 0
    while True:
        # Use very short timeout to avoid blocking
        dev.set_nonblocking(1)
        pkt = dev.read(64)
        dev.set_nonblocking(0)
        if not pkt:
            break
        count += 1
        print(f"  [{label}] flushed packet #{count}: {[hex(b) for b in pkt[:6]]}")
    return count

print("=== Test 1: Spontaneous data (no write, just read) ===")
dev.set_nonblocking(1)
for i in range(5):
    pkt = dev.read(64)
    if pkt:
        print(f"  spontaneous pkt {i}: {[hex(b) for b in pkt[:8]]}")
    else:
        print(f"  spontaneous pkt {i}: (nothing)")
    time.sleep(0.05)
dev.set_nonblocking(0)

print()
print("=== Test 2: STATUS command (cmd=0x01) ===")
flush_all(dev, label="pre-status")

# Write STATUS: [report_id=0, cmd=0x01, zeros...]
status_pkt = bytes([0x00, 0x01] + [0]*63)
r = dev.write(status_pkt)
print(f"  write returned: {r}")
time.sleep(0.15)

print(f"  Reading responses...")
for i in range(5):
    dev.set_nonblocking(1)
    pkt = dev.read(64)
    dev.set_nonblocking(0)
    if pkt:
        print(f"  read {i}: len={len(pkt)} data[0]=0x{pkt[0]:02X} full={[hex(b) for b in pkt[:8]]}")
        if pkt[0] == 0x81:
            print(f"  *** VALID STATUS REPLY! status={pkt[1]}, frames={pkt[2]|(pkt[3]<<8)}")
    else:
        break

print()
print("=== Test 3: Can device receive FEWER bytes in output report? ===")
# Try writing just the report ID + command byte (2 bytes total)
flush_all(dev, label="pre-minimal")

minimal_pkt = bytes([0x00, 0x01])  # report ID + STATUS cmd only
try:
    r2 = dev.write(minimal_pkt)
    print(f"  write([0x00, 0x01]) returned: {r2}")
    time.sleep(0.1)
    pkt2 = dev.read(64, timeout_ms=500)
    if pkt2:
        print(f"  response: {[hex(b) for b in pkt2[:8]]}")
except Exception as e:
    print(f"  write([0x00, 0x01]) failed: {e}")

print()
print("=== Test 4: setAcquisitionParameters fits in 8 bytes? ===")
# All meaningful data for setAcquParams fits in bytes 1-10
# Bytes 9-10 = LO/HI of HIGH_WORD(exposure). For exposure=1000 (0x3E8), HIGH_WORD=0, so bytes 9-10=0
# Truncation to 8 bytes should be fine for this case
flush_all(dev, label="pre-acqparam")

# [report_id, cmd=0x03, scans_lo, scans_hi, blanks_lo, blanks_hi, scanmode, exp_lo, exp_hi, (truncated: exp16_lo, exp16_hi)]
acq_pkt = bytes([0x00, 0x03, 0x01, 0x00, 0x00, 0x00, 0x03, 0xE8, 0x03, 0x00, 0x00] + [0]*54)
r = dev.write(acq_pkt)
print(f"  write setAcquParams returned: {r}  (sent bytes 0-8, truncated at 9)")
time.sleep(0.15)

for i in range(3):
    dev.set_nonblocking(1)
    pkt = dev.read(64)
    dev.set_nonblocking(0)
    if pkt:
        print(f"  read {i}: {[hex(b) for b in pkt[:6]]}")
        if pkt[0] == 0x83:
            print(f"  *** VALID setAcquParams REPLY! errorCode={pkt[1]}")
    else:
        break

dev.close()
print("\nDone.")

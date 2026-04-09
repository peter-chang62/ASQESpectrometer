"""
HID diagnostic: enumerate device, test write/read, dump raw bytes.
"""
import hid

USBD_VID = 0x20E2
USBD_PID = 0x0001
PACKET_SIZE = 64
EXTENDED_PACKET_SIZE = 65

def main():
    print("=== HID device enumeration ===")
    for d in hid.enumerate():
        marker = " <-- TARGET" if d['vendor_id'] == USBD_VID and d['product_id'] == USBD_PID else ""
        print(f"  VID={d['vendor_id']:04X} PID={d['product_id']:04X}  "
              f"usage_page={d.get('usage_page')}  usage={d.get('usage')}  "
              f"path={d.get('path')}  mfr={d.get('manufacturer_string')}  "
              f"prod={d.get('product_string')}{marker}")

    print("\n=== Opening device ===")
    dev = hid.device()
    dev.open(USBD_VID, USBD_PID)
    dev.set_nonblocking(0)
    print(f"  Manufacturer : {dev.get_manufacturer_string()}")
    print(f"  Product      : {dev.get_product_string()}")
    print(f"  Serial       : {dev.get_serial_number_string()}")

    # Try to flush any stale reads (non-blocking)
    dev.set_nonblocking(1)
    flushed = 0
    while True:
        stale = dev.read(EXTENDED_PACKET_SIZE)
        if not stale:
            break
        flushed += 1
        print(f"  Flushed stale packet #{flushed}: {[hex(b) for b in stale]}")
    dev.set_nonblocking(0)

    print("\n=== Sending setAcquisitionParameters (cmd 0x03) ===")
    # Build 65-byte packet: [report_id=0x00, cmd=0x03, ...]
    packet = [0x00] * EXTENDED_PACKET_SIZE
    packet[0] = 0x00   # report ID
    packet[1] = 0x03   # SET_ACQUISITION_PARAMETERS_REQUEST
    packet[2] = 0x01   # num_of_scans LO
    packet[3] = 0x00   # num_of_scans HI
    packet[4] = 0x00   # num_of_blank_scans LO
    packet[5] = 0x00   # num_of_blank_scans HI
    packet[6] = 0x03   # scan_mode
    packet[7] = 0xE8   # exposure LO  (1000 = 0x3E8)
    packet[8] = 0x03   # exposure
    packet[9] = 0x00
    packet[10] = 0x00

    print(f"  Writing {len(packet)} bytes: {[hex(b) for b in packet[:12]]} ...")
    result = dev.write(packet)
    print(f"  write() returned: {result}  (expected {EXTENDED_PACKET_SIZE})")

    # Try reading various sizes
    for size in [64, 65]:
        print(f"\n  Reading {size} bytes...")
        data = dev.read(size, timeout_ms=500)
        print(f"  read({size}) returned {len(data)} bytes")
        if data:
            print(f"  raw bytes: {[hex(b) for b in data]}")
            print(f"  data[0]=0x{data[0]:02X}  data[1]=0x{data[1]:02X}  data[2]=0x{data[2]:02X}")
        else:
            print("  (empty / timeout)")

    dev.close()

if __name__ == "__main__":
    main()

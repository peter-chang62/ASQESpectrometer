"""
Investigate all USB interfaces on the spectrometer device.
Check for WinUSB/bulk interfaces beyond HID.
Also test older hidapi write behavior.
"""
import hid
import ctypes
import ctypes.wintypes

USBD_VID = 0x20E2
USBD_PID = 0x0001

print("=== All HID enumerations for this VID/PID ===")
for d in hid.enumerate(USBD_VID, USBD_PID):
    print(f"  path={d['path']}")
    print(f"  usage_page=0x{d.get('usage_page', 0):04X}  usage=0x{d.get('usage', 0):04X}")
    print(f"  interface_number={d.get('interface_number')}")
    print(f"  release_number={d.get('release_number')}")
    print()

print("=== HID report descriptor ===")
dev = hid.device()
dev.open(USBD_VID, USBD_PID)
try:
    rd = dev.get_report_descriptor()
    print(f"  Report descriptor ({len(rd)} bytes): {[hex(b) for b in rd]}")
except Exception as e:
    print(f"  get_report_descriptor() failed: {e}")

try:
    mfgr = dev.get_manufacturer_string()
    print(f"  Manufacturer: {mfgr}")
except Exception as e:
    print(f"  get_manufacturer_string() failed: {e}")
dev.close()

print()
print("=== Try pyusb (if available) ===")
try:
    import usb.core
    import usb.util
    device = usb.core.find(idVendor=USBD_VID, idProduct=USBD_PID)
    if device is None:
        print("  pyusb: device not found (may need WinUSB/libusb driver)")
    else:
        print(f"  pyusb found device: {device}")
        for cfg in device:
            print(f"  Config {cfg.bConfigurationValue}:")
            for intf in cfg:
                print(f"    Interface {intf.bInterfaceNumber} alt={intf.bAlternateSetting} "
                      f"class=0x{intf.bInterfaceClass:02X} subclass=0x{intf.bInterfaceSubClass:02X} "
                      f"protocol=0x{intf.bInterfaceProtocol:02X}")
                for ep in intf:
                    print(f"      Endpoint 0x{ep.bEndpointAddress:02X} "
                          f"type={usb.util.endpoint_type(ep.bmAttributes)} "
                          f"maxpacket={ep.wMaxPacketSize}")
except ImportError:
    print("  pyusb not installed")
except Exception as e:
    print(f"  pyusb error: {e}")

print()
print("=== Check hidapi output_report_length via older write test ===")
# Try writing exactly 9 bytes vs 65 bytes and compare responses
dev2 = hid.device()
dev2.open(USBD_VID, USBD_PID)

# Test: write STATUS request (cmd 0x01) - minimal command, should get 0x81 back
status_cmd = bytes([0x00, 0x01] + [0]*63)  # 65 bytes
print(f"Writing STATUS cmd (65 bytes): {[hex(b) for b in status_cmd[:4]]}...")
r = dev2.write(status_cmd)
print(f"  write returned: {r}")
resp = dev2.read(64, timeout_ms=500)
print(f"  read: len={len(resp)}, bytes={[hex(b) for b in resp[:6]]}")
print(f"  Expected data[0]=0x81 (STATUS reply)")

dev2.close()

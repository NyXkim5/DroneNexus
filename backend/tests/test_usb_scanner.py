"""
Tests for USB scanner device detection logic.
Run: python -m pytest backend/tests/test_usb_scanner.py -v
      or: python backend/tests/test_usb_scanner.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from usb.scanner import USBScanner, KNOWN_FC_DEVICES, KNOWN_VIDS

passed = 0
failed = 0


def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}")
        print(f"        {e}")
        failed += 1


print("NEXUS USB Scanner Tests")
print("=======================\n")


def test_known_devices_populated():
    assert len(KNOWN_FC_DEVICES) >= 4, f"Expected >= 4 known devices, got {len(KNOWN_FC_DEVICES)}"

test("Known devices list is populated", test_known_devices_populated)


def test_known_vids_set():
    assert 0x10c4 in KNOWN_VIDS, "CP2102 VID should be known"
    assert 0x0483 in KNOWN_VIDS, "STM32 VID should be known"
    assert 0x1a86 in KNOWN_VIDS, "CH340 VID should be known"

test("Known VIDs include common FC chips", test_known_vids_set)


def test_scanner_init():
    scanner = USBScanner()
    assert scanner._known_vids == KNOWN_VIDS

test("USBScanner initializes with known VIDs", test_scanner_init)


def test_scanner_extra_vids():
    scanner = USBScanner(extra_vids={0x1234, 0x5678})
    assert 0x1234 in scanner._known_vids
    assert 0x5678 in scanner._known_vids
    assert 0x10c4 in scanner._known_vids  # built-in still present

test("USBScanner accepts extra VIDs", test_scanner_extra_vids)


def test_get_label_known_device():
    scanner = USBScanner()

    class FakePort:
        vid = 0x0483
        pid = 0x5740
        description = "STM32"

    label = scanner._get_label(FakePort())
    assert "STM32" in label or "Betaflight" in label, f"Bad label: {label}"

test("Get label for known STM32 device", test_get_label_known_device)


def test_is_potential_fc_by_vid():
    scanner = USBScanner()

    class FakePort:
        vid = 0x10c4
        pid = 0xea60
        description = "CP2102 USB to UART"

    assert scanner._is_potential_fc(FakePort()) is True

test("Detect potential FC by VID", test_is_potential_fc_by_vid)


def test_is_potential_fc_by_description():
    scanner = USBScanner()

    class FakePort:
        vid = None
        pid = None
        description = "USB Serial Port"

    assert scanner._is_potential_fc(FakePort()) is True

test("Detect potential FC by description keyword", test_is_potential_fc_by_description)


def test_not_fc_random_device():
    scanner = USBScanner()

    class FakePort:
        vid = 0x9999
        pid = 0x0001
        description = "USB Keyboard"

    assert scanner._is_potential_fc(FakePort()) is False

test("Reject non-FC device", test_not_fc_random_device)


# ---- Summary ----
print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)

from hornethunter_shared.crc import crc16_ccitt_false


def test_canonical_check_value() -> None:
    # The defining check value for CRC-16/CCITT-FALSE.
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_empty_is_init_value() -> None:
    assert crc16_ccitt_false(b"") == 0xFFFF


def test_single_byte_changes_result() -> None:
    assert crc16_ccitt_false(b"\x00") != crc16_ccitt_false(b"\x01")


def test_result_is_16_bit() -> None:
    for data in (b"", b"\x00", b"hornethunter", bytes(range(256))):
        assert 0 <= crc16_ccitt_false(data) <= 0xFFFF

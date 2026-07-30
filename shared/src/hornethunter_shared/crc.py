"""CRC-16/CCITT-FALSE — the HH-Link frame check (FSD Appendix A, §10.2).

Parameters: poly 0x1021, init 0xFFFF, no input/output reflection, xorout 0x0000.
The canonical check value for the ASCII string "123456789" is 0x29B1.

This guards against framing errors — mis-synchronisation, truncation, coalescing —
not channel corruption, which the LoRa PHY CRC already rejects (FSD §15.3).
"""

from __future__ import annotations

_POLY = 0x1021
_INIT = 0xFFFF


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE over `data`, returned as a 16-bit int."""
    crc = _INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ _POLY
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc

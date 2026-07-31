"""Strict parser for the observed 5 HSE manufacturer payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

MANUFACTURER_ID = 0x4743
PACKET_HEADER = b"\x4d\x01"
# Both flags have been observed on a live sensor carrying the identical
# 6x3-byte record format. The flag's semantics are unknown; what matters is
# that each flag carries its OWN counter series (the device switched from
# flag 0x01 to 0x02 mid-session on 2026-07-31, and the counters of the two
# series differ by a constant offset). Callers must treat (flag, counter),
# never counter alone, as the packet identity.
DATA_PACKET_FLAGS = (0x01, 0x02)
PACKET_LENGTH = 24
ENCRYPTED_PAYLOAD_LENGTH = 18
RECORD_LENGTH = 3
RECORD_COUNT = ENCRYPTED_PAYLOAD_LENGTH // RECORD_LENGTH


class PacketDecodeError(ValueError):
    """Raised when a packet does not match the observed protocol contract."""


@dataclass(frozen=True)
class ReadingRecord:
    offset: int
    glucose_mmol: float
    glucose_mg: int
    temperature_c: float
    glucose_raw: int
    temperature_raw: int


@dataclass(frozen=True)
class DecodedPacket:
    counter: int
    flag: int
    checksum: int
    records: Tuple[ReadingRecord, ...]
    raw_hex: str


def decode_payload(payload: bytes, key: int) -> bytes:
    """Reproduce the observed ConvertTools.encode transformation."""
    if len(payload) != ENCRYPTED_PAYLOAD_LENGTH:
        raise PacketDecodeError(
            f"encrypted payload must be {ENCRYPTED_PAYLOAD_LENGTH} bytes"
        )
    if not 0 <= key <= 255:
        raise PacketDecodeError("key must be between 0 and 255")

    bits = "".join(format(byte ^ key, "08b") for byte in payload)
    transformed = list(bits)
    for index in range(len(transformed) - 1):
        if transformed[index + 1] == "0":
            transformed[index] = "1" if transformed[index] == "0" else "0"

    return bytes(
        int("".join(transformed[index : index + 8]), 2)
        for index in range(0, len(transformed), 8)
    )


def parse_records(payload: bytes) -> Tuple[ReadingRecord, ...]:
    if len(payload) != ENCRYPTED_PAYLOAD_LENGTH:
        raise PacketDecodeError(
            f"decoded payload must be {ENCRYPTED_PAYLOAD_LENGTH} bytes"
        )

    records = []
    for offset in range(RECORD_COUNT):
        start = offset * RECORD_LENGTH
        value = int.from_bytes(payload[start : start + RECORD_LENGTH], "big")
        glucose_raw = value >> 10
        temperature_raw = value & 0x3FF
        glucose_mmol = round(glucose_raw * 0.01, 1)
        temperature_c = round(temperature_raw * 0.1 - 40.0, 1)
        records.append(
            ReadingRecord(
                offset=offset,
                glucose_mmol=glucose_mmol,
                glucose_mg=round(glucose_mmol * 18.0),
                temperature_c=temperature_c,
                glucose_raw=glucose_raw,
                temperature_raw=temperature_raw,
            )
        )
    return tuple(records)


def decode_packet(raw: bytes, key: int) -> DecodedPacket:
    if len(raw) != PACKET_LENGTH:
        raise PacketDecodeError(f"manufacturer payload must be {PACKET_LENGTH} bytes")
    if raw[:2] != PACKET_HEADER:
        raise PacketDecodeError(f"unexpected packet header: {raw[:2].hex()}")
    if raw[4] not in DATA_PACKET_FLAGS:
        raise PacketDecodeError(f"unsupported packet flag: {raw[4]}")

    counter = int.from_bytes(raw[2:4], "big")
    decoded = decode_payload(raw[5:23], key)

    # The final byte is preserved for future verification. Its checksum algorithm
    # has not been established from the retained evidence, so claiming validation
    # here would create false confidence.
    return DecodedPacket(
        counter=counter,
        flag=raw[4],
        checksum=raw[23],
        records=parse_records(decoded),
        raw_hex=raw.hex(),
    )

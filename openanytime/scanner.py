"""BLE discovery isolated behind a recoverable scan boundary."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from bleak import BleakScanner

from .config import RuntimeConfig
from .protocol import MANUFACTURER_ID, DecodedPacket, PacketDecodeError, decode_packet

logger = logging.getLogger(__name__)

DiscoverFunction = Callable[..., Awaitable[Any]]


class ScanError(RuntimeError):
    """Raised when the platform BLE scan itself fails."""


@dataclass(frozen=True)
class CapturedSample:
    captured_at: datetime
    name: str
    address: str
    rssi: int
    packet: DecodedPacket


@dataclass(frozen=True)
class ScanOutcome:
    sample: Optional[CapturedSample]
    matching_devices: int
    invalid_packets: int


def _device_name(device: Any, advertisement: Any) -> str:
    return (getattr(advertisement, "local_name", None) or device.name or "").strip()


async def scan_once(
    config: RuntimeConfig,
    *,
    discover: Optional[DiscoverFunction] = None,
) -> ScanOutcome:
    discover_function = discover or BleakScanner.discover
    try:
        devices = await discover_function(
            timeout=config.scan_timeout,
            return_adv=True,
        )
    except Exception as exc:
        raise ScanError(f"BLE scan failed: {exc}") from exc

    if not hasattr(devices, "items"):
        raise ScanError("BLE backend returned an unexpected discovery result")

    target = config.device_name_fragment.casefold()
    candidates = []
    matching_devices = 0
    invalid_packets = 0

    for address, value in devices.items():
        try:
            device, advertisement = value
            name = _device_name(device, advertisement)
            if target not in name.casefold():
                continue

            manufacturer_data = advertisement.manufacturer_data
            if MANUFACTURER_ID not in manufacturer_data:
                continue

            matching_devices += 1
            try:
                packet = decode_packet(manufacturer_data[MANUFACTURER_ID], config.key)
            except PacketDecodeError as exc:
                invalid_packets += 1
                logger.warning("ignored invalid packet from %s: %s", name, exc)
                continue

            candidates.append(
                CapturedSample(
                    captured_at=datetime.now(config.timezone),
                    name=name,
                    address=str(address),
                    rssi=int(advertisement.rssi),
                    packet=packet,
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            invalid_packets += 1
            logger.warning("ignored malformed BLE result: %s", exc)

    sample = max(candidates, key=lambda candidate: candidate.rssi, default=None)
    return ScanOutcome(
        sample=sample,
        matching_devices=matching_devices,
        invalid_packets=invalid_packets,
    )

"""GATT history-pull channel for the 5 HSE sensor.

The broadcast path is the primary data source, but each broadcast packet only
carries a six-record sliding window; any listening gap longer than ~18 minutes
is permanent unless the sensor's on-device buffer is read directly. This module
implements that direct read, mirroring the reference app's reconnect flow:

    connect -> checkId (0x31) -> setDate (0x03) -> pull series (0x37) in a loop

Empirical contract (verified on a live unbound sensor, 2026-07-31):

- The data channel is service 0x1000 (write char 00001002, notify 00001001).
  The FEF5 service is NOT the data channel; pulls there only get a 0x08 nack.
- checkId is a hard gate: without it, setDate and pull get total silence. On
  the observed unbound sensor an all-zero randomB is accepted; a bound sensor
  needs the account-derived randomB (digits 9-12 of the bound account id).
- One notification carries exactly one frame, so the requested record count
  must fit the negotiated MTU. The device answers in 15-byte voltage-mode
  records (33 per 499-byte frame at MTU 512). A 495-byte payload region
  divides evenly by both 11 and 15, so the stride is chosen by which one
  yields physically plausible values.
- Frame layout: [0x37, idLo, idHi, <encrypted records>, sum-checksum].
  Records are transformed with the same bit-flip cipher as broadcasts;
  0xFC blocks are end-of-data padding, 0xFF blocks are skipped.
- Time model: timestamp = session init time + (glucoseId + 1) * interval.
  The glucoseId here is the true session sequence number; the broadcast
  counter lives in a shifted, occasionally rebased numbering and must not be
  mixed with it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import List, Optional, Sequence, Tuple

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

logger = logging.getLogger(__name__)

SERVICE_UUID = "00001000-1212-efde-1523-785feabcd123"
WRITE_UUID = "00001002-1212-efde-1523-785feabcd123"
NOTIFY_UUID = "00001001-1212-efde-1523-785feabcd123"

OP_SET_DATE = 0x03
OP_CHECK_ID = 0x31
OP_PULL_SERIES = 0x37

MAX_RECORDS_PER_FRAME = 45
# Protocol endNumber for this device generation; pulls stop far earlier
# because the device answers with an empty frame past its latest record.
END_NUMBER = 7695


class HistoryPullError(RuntimeError):
    """Raised when the GATT history session fails before any data arrives."""


@dataclass(frozen=True)
class HistoryRecord:
    glucose_id: int
    iw: float
    ib: float
    temperature_c: float
    # The 12-bit coarse glucose field divided by 18. The reference app does
    # not display it; it runs Iw/Ib/T through a compensation algorithm. Kept
    # for completeness because its meaning is not fully established.
    glucose_coarse: float
    trend: int
    error_code: int


def _checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def transform(data: bytes, key: int) -> bytes:
    """The broadcast bit-flip transform; also decrypts history records.

    The sender XORs with key, then flips bit[i] whenever bit[i+1] == 0,
    scanning left to right. Applying the identical forward transform to the
    wire bytes reverses it (this is what the reference parser does).
    """
    bits = "".join(format(byte ^ key, "08b") for byte in data)
    flipped = list(bits)
    for index in range(len(flipped) - 1):
        if flipped[index + 1] == "0":
            flipped[index] = "1" if flipped[index] == "0" else "0"
    return bytes(
        int("".join(flipped[index : index + 8]), 2)
        for index in range(0, len(flipped), 8)
    )


def build_check_id(random_b: Sequence[int]) -> bytes:
    body = bytes([OP_CHECK_ID, *[value & 0xFF for value in random_b]])
    return body + bytes([_checksum(body)])


def build_set_date(now: datetime) -> bytes:
    body = bytes(
        [
            OP_SET_DATE,
            now.year - 1900,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        ]
    )
    return body + bytes([_checksum(body)])


def build_pull_series(start_id: int, count: int) -> bytes:
    body = bytes(
        [OP_PULL_SERIES, start_id & 0xFF, (start_id >> 8) & 0xFF, count & 0xFF]
    )
    return body + bytes([_checksum(body)])


def _sane(temperature: float, glucose_coarse: float) -> bool:
    return -15.0 <= temperature <= 60.0 and 0.0 <= glucose_coarse <= 35.0


def parse_frame(frame: bytes, key: int) -> Tuple[Optional[int], List[HistoryRecord]]:
    """Parse one pull response frame into (start_id, records).

    start_id is None when the frame is not a pull response. The record stride
    (15-byte voltage mode vs 11-byte plain mode) cannot be derived from the
    frame length alone, so both are tried and the one producing more
    physically plausible records wins; the wrong stride scrambles every
    record except rare chance alignments.
    """
    if len(frame) < 4 or frame[0] != OP_PULL_SERIES:
        return None, []
    start_id = frame[1] | (frame[2] << 8)
    region = frame[3:-1]  # last byte is the checksum, not a record

    best: List[HistoryRecord] = []
    best_score = -10**9
    for stride in (15, 11):
        records: List[HistoryRecord] = []
        score = 0
        for offset in range(0, len(region) - stride + 1, stride):
            block = region[offset : offset + stride]
            if block == b"\xfc" * stride:
                break
            if block == b"\xff" * stride:
                continue
            plain = transform(block, key)
            ib = ((plain[0] << 8) | plain[1]) / 100.0
            iw = ((plain[2] << 8) | plain[3]) / 100.0
            temperature = (plain[4] - 40) + plain[5] / 100.0
            if ib > 655 and iw > 655 and temperature > 215:
                break  # end-of-data sentinel
            glucose_raw = ((plain[6] & 0x0F) << 8) | plain[7]
            glucose_coarse = round(glucose_raw / 18.0, 1)
            # Score rewards plausible records and penalizes scrambled ones:
            # with the wrong stride nearly every record is garbage, while a
            # truncated re-parse of a real record can still land one or two
            # lucky "sane" values, so a sane-only count can misfire.
            if _sane(temperature, glucose_coarse):
                score += 1
            else:
                score -= 1
            records.append(
                HistoryRecord(
                    glucose_id=start_id + len(records),
                    iw=iw,
                    ib=ib,
                    temperature_c=round(temperature, 2),
                    glucose_coarse=glucose_coarse,
                    trend=plain[6] >> 4,
                    error_code=plain[8],
                )
            )
        if score > best_score:
            best_score = score
            best = records
    return start_id, best


async def pull_history(
    *,
    key: int,
    device_name_fragment: str,
    start_id: int,
    timezone: tzinfo,
    random_b: Optional[Sequence[int]] = None,
    scan_timeout: float = 120.0,
    frame_timeout: float = 8.0,
) -> List[HistoryRecord]:
    """Pull history records from start_id until the device stops answering.

    Returns an empty list when the device has no new records. Raises
    HistoryPullError when the device cannot be found or the GATT session
    fails; callers running this as a best-effort repair should treat both
    outcomes as "nothing to backfill right now".
    """
    target = device_name_fragment.casefold()
    try:
        device = await BleakScanner.find_device_by_filter(
            lambda candidate, advertisement: target
            in (advertisement.local_name or candidate.name or "").casefold(),
            timeout=scan_timeout,
        )
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        raise HistoryPullError(f"BLE scan failed: {exc}") from exc
    if device is None:
        raise HistoryPullError("device not found in scan window")

    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_notify(_: int, data: bytearray) -> None:
        queue.put_nowait(bytes(data))

    records: List[HistoryRecord] = []
    try:
        async with BleakClient(device, timeout=20.0) as client:
            if client.services.get_service(SERVICE_UUID) is None:
                raise HistoryPullError("device does not expose the 0x1000 data service")
            await client.start_notify(NOTIFY_UUID, on_notify)

            credential = list(random_b) if random_b is not None else [0, 0, 0, 0]
            await client.write_gatt_char(
                WRITE_UUID, build_check_id(credential), response=False
            )
            await asyncio.sleep(1.5)
            await client.write_gatt_char(
                WRITE_UUID, build_set_date(datetime.now(timezone)), response=False
            )
            await asyncio.sleep(1.5)
            # The handshake responses share the notify channel. If one is
            # still queued when the pull loop starts, the loop consumes it as
            # a non-frame and sends the same pull again — and the device then
            # answers every re-sent pull, which cascades into triplicated
            # frames all the way down the stream. Drain before pulling.
            while not queue.empty():
                queue.get_nowait()

            # One notification carries one whole frame, so the record count
            # must keep the frame inside the negotiated MTU.
            count = max(
                1, min(MAX_RECORDS_PER_FRAME, (client.mtu_size - 7) // 11)
            )
            next_id = start_id
            while next_id < END_NUMBER:
                await client.write_gatt_char(
                    WRITE_UUID, build_pull_series(next_id, count), response=False
                )
                # Send exactly one pull per stage, then read until that
                # stage's answer arrives; re-sending on every unrelated
                # frame is what causes the duplication cascade above.
                answered = False
                exhausted = False
                try:
                    while not answered:
                        frame = await asyncio.wait_for(
                            queue.get(), timeout=frame_timeout
                        )
                        frame_start, frame_records = parse_frame(frame, key)
                        if frame_start is None:
                            continue  # unrelated chatter on the same channel
                        if not frame_records:
                            exhausted = True  # empty frame: stream ended
                        else:
                            records.extend(frame_records)
                            next_id = frame_records[-1].glucose_id + 1
                        answered = True
                except asyncio.TimeoutError:
                    break
                if exhausted:
                    break
            await client.stop_notify(NOTIFY_UUID)
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        raise HistoryPullError(f"GATT session failed: {exc}") from exc

    return records

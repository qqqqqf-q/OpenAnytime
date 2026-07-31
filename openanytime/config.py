"""Runtime configuration with validation and conservative defaults."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(ValueError):
    """Raised when runtime configuration is missing or unsafe."""


def default_data_dir() -> Path:
    configured = os.environ.get("OPENANYTIME_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / "cgm-data"


def default_db_path() -> Path:
    configured = os.environ.get("OPENANYTIME_DB")
    if configured:
        return Path(configured).expanduser()
    return default_data_dir() / "cgm.db"


def _environment_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _environment_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: Path
    key: int
    device_name_fragment: str
    timezone: ZoneInfo
    scan_timeout: float
    scan_interval: float
    reading_interval_seconds: int

    def validate(self) -> "RuntimeConfig":
        if not 0 <= self.key <= 255:
            raise ConfigurationError("OPENANYTIME_KEY must be between 0 and 255")
        if not self.device_name_fragment.strip():
            raise ConfigurationError("device name fragment cannot be empty")
        if not math.isfinite(self.scan_timeout) or self.scan_timeout <= 0:
            raise ConfigurationError("scan timeout must be greater than zero")
        if not math.isfinite(self.scan_interval) or self.scan_interval <= 0:
            raise ConfigurationError("scan interval must be greater than zero")
        if self.reading_interval_seconds <= 0:
            raise ConfigurationError("reading interval must be greater than zero")
        return self


def load_runtime_config(
    *,
    db_path: Optional[str] = None,
    key: Optional[int] = None,
    device_name_fragment: Optional[str] = None,
    timezone_name: Optional[str] = None,
    scan_timeout: Optional[float] = None,
    scan_interval: Optional[float] = None,
) -> RuntimeConfig:
    if key is None:
        raw_key = os.environ.get("OPENANYTIME_KEY")
        if raw_key is None:
            raise ConfigurationError(
                "OPENANYTIME_KEY is required; use the sensor-specific sureClose value"
            )
        try:
            key = int(raw_key)
        except ValueError as exc:
            raise ConfigurationError("OPENANYTIME_KEY must be an integer") from exc

    zone_name = timezone_name or os.environ.get(
        "OPENANYTIME_TIMEZONE", "Asia/Shanghai"
    )
    try:
        timezone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"unknown timezone: {zone_name}") from exc

    config = RuntimeConfig(
        db_path=Path(db_path).expanduser() if db_path else default_db_path(),
        key=key,
        device_name_fragment=(
            device_name_fragment or os.environ.get("OPENANYTIME_DEVICE_NAME", "")
        ).strip(),
        timezone=timezone,
        scan_timeout=(
            scan_timeout
            if scan_timeout is not None
            else _environment_float("OPENANYTIME_SCAN_TIMEOUT", 90.0)
        ),
        scan_interval=(
            scan_interval
            if scan_interval is not None
            else _environment_float("OPENANYTIME_SCAN_INTERVAL", 120.0)
        ),
        reading_interval_seconds=_environment_int(
            "OPENANYTIME_READING_INTERVAL", 180
        ),
    )
    return config.validate()

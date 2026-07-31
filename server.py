#!/usr/bin/env python3
"""Read-only local API and static server for OpenAnytime."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from openanytime.config import default_db_path
from openanytime.storage import StorageError, open_database, validate_schema

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("OPENANYTIME_DB", str(default_db_path()))).expanduser()
WEB_ROOT = Path(
    os.environ.get("OPENANYTIME_WEB_ROOT", str(ROOT / "web" / "dist"))
).expanduser()
HOST = os.environ.get("OPENANYTIME_HOST", "127.0.0.1")
MAX_LIMIT = 2000

logger = logging.getLogger(__name__)


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("OPENANYTIME_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("OPENANYTIME_PORT must be between 1 and 65535")
    return port


def parse_limit(value: Optional[str], *, default: int = 500) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= parsed <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return parsed


def validate_since(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("since must be an ISO 8601 timestamp") from exc
    return value


def db_query(sql: str, params: Sequence[Any] = ()):
    with closing(open_database(DB, read_only=True)) as connection:
        validate_schema(connection)
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def resolve_static_path(request_path: str) -> Path:
    root = WEB_ROOT.expanduser().resolve()
    relative = unquote(request_path).lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes web root") from exc
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAnytime/0.1"

    def log_message(self, message: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), message % args)

    def _send_headers(
        self,
        status: int,
        content_type: str,
        content_length: int,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send_headers(
            status,
            "application/json; charset=utf-8",
            len(body),
        )
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("client disconnected before JSON response completed")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _serve_static(self, path: str) -> None:
        try:
            target = resolve_static_path(path)
        except ValueError:
            self._error(400, "invalid path")
            return

        if not target.is_file():
            self._error(404, "not found")
            return

        body = target.read_bytes()
        mime, _ = mimetypes.guess_type(target.name)
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type = f"{content_type}; charset=utf-8"
        cache_control = (
            "public, max-age=31536000, immutable"
            if target.parent.name == "assets"
            else "no-cache"
        )
        self._send_headers(200, content_type, len(body), cache_control=cache_control)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("client disconnected before static response completed")

    def _readings(self, query: dict) -> None:
        try:
            limit = parse_limit(query.get("limit", [None])[0])
            since = validate_since(query.get("since", [None])[0])
        except ValueError as exc:
            self._error(400, str(exc))
            return

        if since:
            rows = db_query(
                """
                SELECT * FROM readings
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (since, limit),
            )
        else:
            rows = db_query(
                "SELECT * FROM readings ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows.reverse()

        scans = db_query(
            """
            SELECT timestamp, counter, rssi, record_count
            FROM scans
            ORDER BY timestamp DESC
            LIMIT 50
            """
        )
        self._json({"readings": rows, "scans": scans, "count": len(rows)})

    def _stats(self) -> None:
        rows = db_query(
            """
            SELECT
                COUNT(*) AS total,
                MIN(glucose_mmol) AS g_min,
                MAX(glucose_mmol) AS g_max,
                ROUND(AVG(glucose_mmol), 1) AS g_avg,
                MIN(temperature_c) AS t_min,
                MAX(temperature_c) AS t_max,
                ROUND(AVG(temperature_c), 1) AS t_avg,
                MIN(timestamp) AS first_ts,
                MAX(timestamp) AS last_ts
            FROM readings
            """
        )
        self._json(rows[0] if rows else {})

    def _latest(self) -> None:
        reading = db_query(
            "SELECT * FROM readings ORDER BY timestamp DESC LIMIT 1"
        )
        scan = db_query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT 1")
        self._json(
            {
                "reading": reading[0] if reading else None,
                "scan": scan[0] if scan else None,
            }
        )

    def do_GET(self) -> None:
        request = urlparse(self.path)
        try:
            if request.path == "/api/readings":
                self._readings(parse_qs(request.query))
            elif request.path == "/api/stats":
                self._stats()
            elif request.path == "/api/latest":
                self._latest()
            elif request.path.startswith("/api/"):
                self._error(404, "unknown API endpoint")
            elif request.path == "/favicon.ico":
                self._send_headers(204, "image/x-icon", 0, cache_control="public")
            else:
                self._serve_static(request.path)
        except (StorageError, sqlite3.Error) as exc:
            logger.error("database request failed: %s", exc)
            self._error(503, "local database is unavailable")
        except Exception:
            logger.exception("unexpected request failure")
            self._error(500, "unexpected server error")


def validate_startup() -> Tuple[Path, Path]:
    database = DB.expanduser().resolve()
    web_root = WEB_ROOT.expanduser().resolve()
    with closing(open_database(database, read_only=True)) as connection:
        schema = validate_schema(connection)
        if schema.is_legacy:
            logger.warning("database uses legacy schema version 0; run db_tool.py check")
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise StorageError(f"database quick_check failed: {integrity}")
    if not (web_root / "index.html").is_file():
        raise FileNotFoundError(
            f"frontend build missing at {web_root}; run `pnpm --dir web build`"
        )
    return database, web_root


def main() -> int:
    log_level_name = os.environ.get("OPENANYTIME_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, None)
    if not isinstance(log_level, int):
        print(f"invalid OPENANYTIME_LOG_LEVEL: {log_level_name}", file=sys.stderr)
        return 2
    try:
        port = parse_port(os.environ.get("OPENANYTIME_PORT", "8520"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        database, web_root = validate_startup()
    except (StorageError, sqlite3.Error, FileNotFoundError) as exc:
        logger.critical("server refused to start: %s", exc)
        return 2

    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        logger.critical("server failed to bind %s:%d: %s", HOST, port, exc)
        return 2
    logger.info("server=http://%s:%d", HOST, port)
    logger.info("database=%s mode=read-only", database)
    logger.info("web_root=%s", web_root)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("server stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

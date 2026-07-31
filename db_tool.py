#!/usr/bin/env python3
"""Explicit, auditable database lifecycle commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from openanytime.config import default_db_path
from openanytime.storage import (
    StorageError,
    backup_database,
    database_summary,
    initialize_database,
    sha256_file,
)


def _summary_payload(summary):
    payload = asdict(summary)
    payload["path"] = str(summary.path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenAnytime database operations")
    parser.add_argument("--db", default=str(default_db_path()))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="read-only schema and integrity check")
    subparsers.add_parser("init", help="create a new database; refuses existing paths")
    backup = subparsers.add_parser("backup", help="create and verify a new backup")
    backup.add_argument("--output", help="new backup path")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    database = Path(args.db).expanduser().resolve()

    try:
        if args.command == "init":
            initialize_database(database)
            summary = database_summary(database)
        elif args.command == "check":
            summary = database_summary(database)
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = (
                Path(args.output).expanduser().resolve()
                if args.output
                else database.with_name(f"{database.name}.{timestamp}.backup")
            )
            summary = backup_database(database, destination)

        payload = _summary_payload(summary)
        payload["sha256"] = sha256_file(summary.path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except StorageError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

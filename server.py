#!/usr/bin/env python3
"""
CGM Web 服务器 — API + 前端
用法: /tmp/bleak-venv/bin/python3 server.py
然后浏览器打开 http://localhost:8520
"""
import sqlite3, json, os, time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "cgm.db")
TZ = timezone(timedelta(hours=8))  # UTC+8


def db_query(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, path):
        try:
            with open(os.path.join(DIR, path), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def _static(self, path, mime):
        try:
            with open(os.path.join(DIR, path), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == "/" or path == "/index.html":
            self._html("dashboard.html")
        elif path == "/api/readings":
            limit = int(qs.get("limit", [500])[0])
            since = qs.get("since", [None])[0]

            if since:
                rows = db_query(
                    "SELECT * FROM readings WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
                    (since, limit),
                )
            else:
                rows = db_query(
                    "SELECT * FROM readings ORDER BY timestamp DESC LIMIT ?", (limit,)
                )
                rows.reverse()

            # 汇总
            scans = db_query(
                "SELECT timestamp, counter, rssi, record_count FROM scans ORDER BY timestamp DESC LIMIT 50"
            )

            self._json({"readings": rows, "scans": scans, "count": len(rows)})

        elif path == "/api/stats":
            rows = db_query("""
                SELECT
                    COUNT(*) as total,
                    MIN(glucose_mmol) as g_min,
                    MAX(glucose_mmol) as g_max,
                    ROUND(AVG(glucose_mmol), 1) as g_avg,
                    MIN(temperature_c) as t_min,
                    MAX(temperature_c) as t_max,
                    ROUND(AVG(temperature_c), 1) as t_avg,
                    MIN(timestamp) as first_ts,
                    MAX(timestamp) as last_ts
                FROM readings
            """)
            self._json(rows[0] if rows else {})

        elif path == "/api/latest":
            row = db_query("SELECT * FROM readings ORDER BY timestamp DESC LIMIT 1")
            scan = db_query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT 1")
            self._json({"reading": row[0] if row else None, "scan": scan[0] if scan else None})

        else:
            self.send_error(404)


def main():
    # 确保有数据库
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            counter INTEGER,
            reading_index INTEGER,
            glucose_mmol REAL,
            glucose_mg INTEGER,
            temperature_c REAL,
            rssi INTEGER,
            raw_hex TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT, address TEXT, rssi INTEGER,
            counter INTEGER, raw_hex TEXT, record_count INTEGER
        )
    """)
    conn.commit()
    conn.close()

    port = 8520
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"CGM Server → http://localhost:{port}")
    print(f"数据目录: {DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

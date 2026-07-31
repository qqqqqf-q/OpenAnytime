#!/bin/bash
# 持续监控 CGM，每2分钟扫描一次，数据落盘 JSONL
# 用法: ./monitor.sh
# 停止: Ctrl+C

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON=/tmp/bleak-venv/bin/python3

echo "Anytime CGM 持续监控"
echo "数据目录: $SCRIPT_DIR"
echo "按 Ctrl+C 停止"
echo "---"

while true; do
    $PYTHON "$SCRIPT_DIR/decode.py" 2>&1
    sleep 120  # 每2分钟
done

#!/bin/sh
# 运行播报的包装：自动使用项目内虚拟环境（兼容 macOS / Linux / NAS）
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python main.py "$@"

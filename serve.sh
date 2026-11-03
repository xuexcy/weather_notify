#!/bin/sh
# 启动本地服务：地址/端口取 conf/config.json 的 server.host / server.port
#   ./serve.sh                        → 用配置里的地址与端口
#   ./serve.sh --port 9000            → 临时指定端口（不改配置）
#   ./serve.sh --host 192.168.1.100   → 允许局域网访问（NAS 部署用，写网卡 IP）
# macOS 会自动打开浏览器；Linux/NAS 无桌面，不加 --open。
cd "$(dirname "$0")" || exit 1
if [ "$(uname)" = "Darwin" ]; then
  exec .venv/bin/python src/server.py "$@" --open
else
  exec .venv/bin/python src/server.py "$@"
fi

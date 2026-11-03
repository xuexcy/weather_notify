# 部署与启停

## 监听地址

服务**默认只监听 `127.0.0.1`**（`config_loader.DEFAULT_HOST`）。部署在 NAS 上时，
从别的机器访问必须改监听地址，下面两种方式任选其一：

| 方式 | 做法 | 重启后是否保留 |
|---|---|---|
| 配置（推荐） | `conf/config.json` 的 `server.host` 设为 NAS 的局域网 IP（如 `192.168.1.100`） | 是 |
| 命令行 | `./serve.sh --host 192.168.1.100` 或 `python src/server.py --host 192.168.1.100` | 是（重启会重放启动参数） |

**建议写具体网卡 IP，而不是 `0.0.0.0`**：`0.0.0.0` 会监听所有网卡，
在装了 Tailscale / OpenVPN / Docker 的 NAS 上等于把设置页一并开放到
`100.64.x`、`10.8.x`、`172.x` 等网段，通常没必要。
日志会直接打印可点击的 URL；设置页「当前地址」则按浏览器实际使用的主机名显示。
不要再靠「本机出口 IP」去猜地址——有旁路由时路由表选出的可能是 `198.18.x` 这类虚拟地址。

## 以 fnOS NAS 为例

```bash
# 项目放在 /vol2/1000/agent沙箱/xuechengyun_project/weather_notify
cd "/vol2/1000/agent沙箱/xuechengyun_project/weather_notify"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 确认 conf/ 下有 config.json 和 ed25519-private.pem（含凭据，权限 600）
.venv/bin/python src/server.py --host 192.168.1.100   # 或写进 conf/config.json 后直接 ./serve.sh
# 浏览器打开 http://192.168.1.100:18770/settings
```

> ✅ 设置页**默认开启访问密码鉴权**（见 [auth.md](auth.md)）：第一次打开
> `http://192.168.1.100:18770/settings` 会跳到 `/login`，设置密码后才进得去；
> 首页天气仍可公开查看。绑到局域网 IP 仍建议只在可信网段内使用、别用弱口令；
> 如只需临时访问，可保持 `127.0.0.1` 并用 SSH 端口转发
> （`ssh -L 18770:127.0.0.1:18770 user@nas`）。

定时任务**不需要在这里额外配置**：只要 `serve.sh` 这个进程在跑，设置页里开启的
日常播报与预警轮询就会按点执行；进程一停就全部停止。详见 [scheduling.md](scheduling.md)。

## 服务启停（手动）

项目**没有自动拉起**：服务在，全部功能在；服务不在，全部功能失效。

| 操作 | 命令 |
|---|---|
| 启动 | `cd "<项目目录>" && nohup ./serve.sh >> logs/server.log 2>&1 < /dev/null &` |
| 停止 | `kill "$(cat "<项目目录>/logs/server.pid")"` |
| 查是否在跑 | `curl -s -o /dev/null -w "%{http_code}" http://<NAS IP>:18770/api/status` |
| 看日志 | `tail -50 "<项目目录>/logs/server.log"` |

`<项目目录>` 换成实际绝对路径。`serve.sh` 用 `exec` 启动 Python，所以
`logs/server.pid` 里就是服务进程本身；通过设置页 `/api/restart` 换端口是原地
`execv`，PID 保持不变。若自行配了进程守护，语义仍是「服务在 → 功能在」。

## 用 git 更新已部署的实例

```bash
cd "<项目目录>"
git pull                      # 代码在 src/，web/ tests/ docs/ 一并更新
.venv/bin/pip install -r requirements.txt
kill "$(cat logs/server.pid)"; nohup ./serve.sh >> logs/server.log 2>&1 < /dev/null &
```

`conf/`（凭据、私钥、状态文件）和 `logs/` 不进版本库，`git pull` 不会覆盖它们。

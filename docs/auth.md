# 设置页访问密码

设置页能看到和风凭据、企业微信 Webhook、MagicPush 令牌，也能触发推送和重启服务，
所以**默认开启鉴权**。服务启动后第一次打开 `/settings` 会 302 到 `/login`：

- 还没设密码 → 显示「首次使用 · 设置访问密码」（至少 6 位），设完自动登录进设置页
- 已设密码 → 输入密码登录，登录状态在该浏览器保持 **7 天**（`auth.session_hours`）
- 设置页右上角「退出登录」立即清 cookie；「访问密码」卡片里可改密码、关闭鉴权

实现见 `src/auth.py`（纯标准库，无新依赖）：

| 项 | 做法 |
|---|---|
| 密码存储 | PBKDF2-HMAC-SHA256（20 万轮）+ 随机盐，只存哈希与盐，不存明文 |
| 会话 | 无状态 cookie：`base64(payload).HMAC-SHA256`，payload 只带过期时间 |
| 签名密钥 | `conf/config.json` 的 `auth.secret`，服务重启 / 换端口不变 → 不掉线 |
| Cookie | `HttpOnly; SameSite=Strict; Path=/`；内网是 http，**不加 Secure** |
| 防暴力 | 同一 IP 连续 5 次失败锁 60 秒；改密码轮换密钥，其他设备会话立即失效 |
| 防跨站 | 敏感 POST 校验 `Origin` 与 `Host` 一致（curl 无 Origin 时放行，便于命令行调用） |

## 哪些接口需要登录

首页保持公开：

| 无需登录 | 需要登录 |
|---|---|
| `/`、`/login`、`/style.css`、`/favicon.ico` | `/settings` |
| `/api/status`、`/api/weather`、`/api/auth/status` | `/api/state`、`/api/schedule` |
| `/api/city`、`/api/location`、`/api/geoip`（首页「更改城市」用） | `/api/config`、`/api/message`、`/api/test`、`/api/run`、`/api/restart` |
| `/api/auth/login`、`/api/auth/setup` | `/api/auth/password`、`/api/auth/toggle`、`/api/auth/logout` |

> 首页「更改城市」的三个接口可以公开写位置（不含凭据、不触发推送），这是刻意的：
> 首页本来就是公开看天气的。`/api/geoip` 只在请求显式带 `apply=true` 时才写配置。
> `/api/state` 返回的 `auth` 段是脱敏视图（只有 `enabled` / `session_hours` / `has_password`），
> 密码哈希、盐、签名密钥都不会下发。

## 忘记密码

编辑 `conf/config.json`，把 `auth.password_hash` 与 `auth.salt` 清空，
重启服务后再打开 `/settings` 就会回到「首次使用」流程重新设置。
（不要删 `auth.secret`，那只是会话签名密钥。）

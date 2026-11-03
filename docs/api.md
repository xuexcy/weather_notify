# 接口

| 方法 | 路径 | 登录 | 说明 |
|---|---|---|---|
| GET | `/`、`/login` | — | 首页天气面板 / 登录页 |
| GET | `/api/weather` | — | 结构化天气 JSON（10 分钟新鲜期；过期先返回旧数据 `stale=true` 并后台刷新；`?fresh=1` 强制刷新有 15 秒全局限流） |
| GET | `/api/status` | — | 配置状态：缺项、通道、位置、最近一次推送 |
| GET | `/api/auth/status` | — | 鉴权状态：开关、是否已设密码、是否已登录、会话时长 |
| GET | `/api/state` | ✅ | 原始配置（`auth` 段脱敏）+ 状态（设置页用） |
| GET | `/api/schedule` | ✅ | 调度状态：播报时间点、下次/上次触发、预警轮询间隔与下次检查 |
| POST | `/api/auth/setup` | — | 首次设置访问密码（仅未设密码时可用），成功后下发 cookie |
| POST | `/api/auth/login` | — | 校验密码并下发 cookie（同一 IP 连续 5 次失败锁 60 秒） |
| POST | `/api/auth/logout` | ✅ | 清除 cookie |
| POST | `/api/auth/password` | ✅ | 改密码（校验原密码），轮换签名密钥让其他设备掉线 |
| POST | `/api/auth/toggle` | ✅ | 开关设置页鉴权 |
| POST | `/api/config` | ✅ | 保存配置（通道列表按 id 合并，不会误删未提交的通道） |
| POST | `/api/geoip` | — | 按公网 IP 定位；只有 `apply=true` 才写配置（首页 / 设置页都显式传） |
| POST | `/api/city` | — | 按名称搜城市（body `{keyword}`，可带 `config`），返回省 / 市 / 区县候选 |
| POST | `/api/location` | — | 直接用候选城市替换位置（body 为 `/api/city` 返回的一项），立即生效 |
| POST | `/api/message` | ✅ | 生成推送消息预览（不推送）。`mode=broadcast`（默认）用真实天气；`mode=alert` 用示例预警 |
| POST | `/api/test` | ✅ | 测试推送：**连发两条** —— 日常播报（真实天气）+ 天气预警（示例预警），都带「🧪 测试」前缀；**只发勾了「测试」用途的通道**（`roles` 含 `test`），只勾正式的通道一条都不碰；不要求和风凭据（无 key 时走 Open-Meteo） |
| POST | `/api/test-channel` | ✅ | 单通道连通性测试：只发一条极简「🧪 连通性测试」消息（不取天气、不渲染模板），走该通道自己的地址。body `{id, config}`，按通道 id 匹配（优先用页面提交的凭据，未保存也能测） |
| POST | `/api/test-qweather` | ✅ | 和风 API 连通性测试：真实调用一次 GeoAPI，同时验证 API Host、项目 ID、凭据 ID 与 Ed25519 私钥。body `{config}` 优先用页面提交的未保存凭据 |
| POST | `/api/qweather/key` | ✅ | 生成 / 替换和风 Ed25519 密钥对（写 `conf/` 下两个 pem）。body `{action}`：`generate` 现场生成一对；`replace` 用 `{private_key}` 粘贴的私钥（PEM 或 ssh-keygen 格式），公钥由私钥推导。旧私钥自动备份，响应只含公钥不含私钥 |
| POST | `/api/channel/add` | ✅ | 添加通道实例：body `{type, roles?}`（`type`: `wecom` \| `magicpush`；`roles` 可选，默认 `["prod"]`），往 `push.channels` 追加一项并默认启用，同类可加多个、名称自动去重；用途（正式/测试，可同时勾）添加后在卡片上改 |
| POST | `/api/channel/remove` | ✅ | 删除通道实例：body `{id}`，从 `push.channels` 移除（凭据一并丢弃），并把 id 记进删除墓碑 `push.removed`，防止旧快照保存时把通道写回 |
| POST | `/api/run` | ✅ | 立即播报（拉天气 + 推送）。设置页「定时播报 → 立即播报一次」有按钮，也可 curl 触发 |
| POST | `/api/run-warning` | ✅ | 立即检查一次预警（只有新预警才推送） |
| POST | `/api/restart` | ✅ | 保存端口并原地重启服务（body 可带 `config`，缺省只改端口） |

> 标 ✅ 的接口和 `/settings` 需要登录，未登录返回 401（页面跳 `/login`）；
> 敏感 POST 还会校验 `Origin` 与 `Host` 一致，跨站调用返回 403。

## 命令行手动触发

定时任务只由本服务的调度线程触发；看到「立即播报一次」「立即检查预警」按钮带二次确认，
是为了避免误点推到所有通道。命令行手动补一次要先登录拿 cookie：

```bash
curl -c /tmp/wn.cookie -X POST -H 'Content-Type: application/json' \
  -d '{"password":"你的密码"}' http://127.0.0.1:18770/api/auth/login

curl -b /tmp/wn.cookie -X POST http://127.0.0.1:18770/api/run
```

部署到 NAS 且绑定了具体网卡 IP 时，把 `127.0.0.1` 换成 NAS 的 IP。

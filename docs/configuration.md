# 配置

设置页能改的东西都会写进 `conf/config.json`，正常使用**不需要手动编辑**。
文件权限 600，整份 `conf/` 已在 `.gitignore` 里，不会进版本库。

```
conf/
├── config.json          # 主配置
├── ed25519-private.pem  # 和风 JWT 凭据私钥（权限 600，设置页可生成 / 替换）
├── ed25519-public.pem   # 对应公钥（由私钥推导），上传到和风控制台
├── alert_state.json     # 预警去重状态（自动生成）
├── last_run.json        # 最近一次推送记录（首页「最近一次推送」）
└── schedule_state.json  # 定时触发状态（自动生成）
```

## 字段

| 字段 | 说明 |
|---|---|
| server.port | 本地网页服务端口（默认 18770，设置页最底部可改，改完点「重启服务」生效） |
| server.host | 监听地址（默认 `127.0.0.1`；NAS 部署写局域网 IP 如 `192.168.1.100`） |
| auth.enabled | 设置页鉴权开关（默认 `true`，见 [auth.md](auth.md)） |
| auth.password_hash / salt | PBKDF2 密码哈希与随机盐，由登录页生成，不要手填 |
| auth.secret | cookie 签名密钥，服务首次启动自动生成，不要外泄 |
| auth.session_hours | 登录保持时长（默认 168 小时 = 7 天） |
| schedule.enabled | 内置定时播报开关（默认 `false`，见 [scheduling.md](scheduling.md)） |
| schedule.times | 播报时间点数组，北京时间 `HH:MM`，如 `["07:30","18:00"]`（最多 12 个） |
| schedule.warning_enabled | 预警轮询开关（默认 `false`） |
| schedule.warning_interval_min | 预警轮询间隔，1–120 分钟（默认 10） |
| qweather.api_host | 控制台「设置」里的专属 API Host，如 `xxx.re.qweatherapi.com` |
| qweather.project_id | 控制台「项目管理」的项目 ID |
| qweather.credential_id | JWT 凭据 ID（kid） |
| location | 送 API 的位置值：「经度,纬度」（定位或搜索写入）或和风 LocationID |
| location_name | 显示用城市名（区县级时是区县名，如「公安」） |
| location_admin | 显示用的省 / 市，如「湖北省 荆州」 |
| location_source | `ip`（自动定位）/ `manual`（手动设置） |
| location_lat / location_lon | 定位得到的坐标 |
| location_ip | 定位时命中的公网出口 IP |
| location_note | 定位不可靠时的提示文案（多出口 / 非中国大陆） |
| location_updated | 定位时间 |
| hourly_hours | 逐小时预报显示条数（默认 6） |
| forecast_days | 逐日预报天数：3/7/10/15/30（默认 7；超 7 天需和风相应订阅，权限不足自动回落 3 天） |
| indices_types | 生活指数类型（默认 `3,5,9` = 穿衣/紫外线/感冒；也可 1 运动 / 2 洗车） |
| alert_min_level | 推送的最低预警等级：白色/蓝色/黄色/橙色/红色（默认蓝色） |
| alert_types | 只推指定类型，如 `["大风","暴雨"]`；空数组 = 全部 |
| alert_resend_hours | 同一条预警重复推送间隔（默认 6 小时，等级升级则立即重推） |
| message.broadcast_title / broadcast_body | 日常播报的标题与正文模板（见 [message-template.md](message-template.md)） |
| message.alert_title / alert_body | 天气预警的标题与正文模板 |

## 提醒通道 `push`

通道是**列表**，不是固定字段——同类可以添加多个，每个实例有自己的 id：

```json
"push": {
  "channels": [
    {
      "id": "a1b2c3d4",
      "type": "wecom",
      "name": "家庭群",
      "enabled": true,
      "roles": ["prod"],
      "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
      "msg_type": "text"
    },
    {
      "id": "e5f6a7b8",
      "type": "magicpush",
      "name": "测试设备",
      "enabled": true,
      "roles": ["prod", "test"],
      "base_url": "http://192.168.1.100:8083",
      "token": "..."
    }
  ],
  "removed": ["9f8e7d6c"]
}
```

| 字段 | 说明 |
|---|---|
| id | 通道实例标识，添加时自动生成，保存/删除按它匹配 |
| type | `wecom`（企业微信机器人）或 `magicpush`（MagicPush 魔法推送） |
| name | 显示名，可改；重名自动加序号 |
| enabled | 是否启用 |
| roles | 用途列表：`["prod"]` 正式 / `["test"]` 测试 / `["prod","test"]` 两者兼任 |
| wecom 专有 | `webhook`（群机器人地址）、`msg_type`（`text` 纯文本 / `markdown`） |
| magicpush 专有 | `base_url`（服务地址，自动拼 `/api/push`）、`token`（接口令牌） |
| push.removed | 已删除通道的 id 墓碑（最近 32 个），防止旧页面快照把通道写回 |

> 老配置（`push.wecom` / `push.magicpush` 固定字段、单值 `role`）在启动时自动迁移成本结构，
> 迁移前会先备份原文件（`config.json.bak_*`）。未知通道类型、未知字段一律被清洗掉。

## 生成和风密钥对

在设置页「天气数据」卡片的密钥区操作，**不需要命令行**：

| 按钮 | 做什么 | 什么时候用 |
|---|---|---|
| **生成密钥对** | 现场生成一对 Ed25519 密钥，公钥直接显示出来 | 从零接入和风：生成 → 复制公钥 → 和风控制台「创建凭据 → JWT」粘贴 → 拿到凭据 ID |
| **替换私钥** | 粘贴已有私钥（PEM 或 `ssh-keygen -t ed25519` 生成的都行），公钥由私钥自动推导 | 手里已有密钥对，或换和风项目 |

- 公钥**永远由私钥推导**，不存在「两段分别填、结果对不上」的可能；
- 换密钥后立即生效（无需重启），但**和风控制台里的旧公钥随即失效**，要记得把新公钥重新上传；
- 替换时旧私钥自动备份为 `ed25519-private.pem.bak_时间戳`，填错了能换回来；
- 私钥永不出现在页面或任何接口响应里，权限自动收敛到 600。

也能用命令行（等价）：

```bash
openssl genpkey -algorithm ED25519 -out conf/ed25519-private.pem
openssl pkey -pubout -in conf/ed25519-private.pem
```

设置页「天气数据」卡片的**「测试 API」**按钮会真实调用一次 GeoAPI，
同时验证 API Host、项目 ID、凭据 ID 与 Ed25519 私钥是否配套，不用真发一条推送去试错。

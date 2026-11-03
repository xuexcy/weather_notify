# weather-notify

和风天气 → 企业微信机器人 / MagicPush 天气提醒（含官方灾害预警）。

由 [cuijianzhuang/weather_push](https://github.com/cuijianzhuang/weather_push) 裁剪重写：
仅保留天气功能，删除纪念日/恋爱天数/彩虹屁/一言/Telegram/公众号/邮件/WxPusher 等无关模块。

## 特性

- 和风天气新版认证：JWT（Ed25519），不再使用 devapi + key 老方式
- 官方灾害预警（暴雨/台风/大风/寒潮等），有预警时预警置顶推送
- 日常播报：实况 + 空气质量 + 逐日 + 逐小时 + 生活指数
- **无和风凭据也能用**：自动降级 Open-Meteo（无需注册），首页照样有天气，只是没有官方预警
- **单一服务架构**：`server.py` 是唯一进程，首页、设置页、日常播报、预警轮询全在里面
  - 服务在 → 全部功能在；服务停 → 网页和所有定时推送一起停止
  - 定时全部在设置页「定时播报」配置，**不需要 1Panel / cron / 任何外部定时器**
- `main.py` 只作 CLI 手动调试（单次播报 / 单次预警检查），不参与定时调度
- 私密数据全部隔离在 `conf/`（已 gitignore，`config.json` 与私钥权限 600）
- **首次启动按公网 IP 自动定位城市**，无需自己查 LocationID
- **网页界面**：首页看天气 / 设置页填凭据，提醒通道可添加多个实例（含同类多个）
- **设置页访问密码**：PBKDF2 密码哈希 + HMAC 签名 cookie，首页天气仍可公开查看
- **消息模板可改**：日常播报与天气预警各有标题/正文模板，用 `{变量}` 拼装
- **企业微信可选纯文本**：通过微信接收企业微信消息时不渲染 markdown，选纯文本即可

## 网页界面

```bash
./serve.sh          # 启动后自动打开 http://127.0.0.1:18770
```

启动后首页可直接看天气；**第一次点「设置」会跳到 `/login` 让你设置访问密码**。

| 路由 | 内容 |
|---|---|
| `/` | 首页：当前天气（大字温度、风/湿度/空气）、预警、逐小时、逐日（超过 5 天自动切换成曲线图）、生活指数 |
| `/login` | 登录页：首次使用设置访问密码，之后输入密码进入设置页 |
| `/settings` | 设置页：和风凭据、城市位置、提醒通道（点「＋ 添加通道」接入）、播报内容、**定时播报**、**访问密码**、服务端口 |

首页只做一件事：**看天气**。不出现配置引导、推送状态、命令行提示，
所有填写与操作都在 `/settings`，首页顶部固定一个「设置」入口。
**首页无需登录**，设置页需要（见「设置页访问密码」）。

- 有预警时按等级配色置顶：橙色/红色用红调卡片，蓝色/黄色用黄调卡片，徽章颜色对应等级
- 逐日预报 ≤5 天用逐行列表；>5 天切换成「一天一列 + 高低温曲线」，列多到放不下时横向滚动
  （右边缘渐隐提示可滑动）。和风逐日空气质量只有 3 天，其余天该行显示 `–`；
  该行格式为「等级(指数)」，如 `优(50)` `良(56)`，四字等级（轻度污染等）在窄列自动换成两行，且不会在词中间断字
- 数据来自 Open-Meteo 兜底时，显示一条中性提示说明原因，并给出「接入和风」入口
- 「更改城市」打开搜索面板：输入城市 / 区县名，从列表里挑一个（**可精确到区县**）；
  也可以在里面点「按 IP 定位」按公网 IP 重新定位
- 深色模式跟随系统

位置怎么定：
- 顶部与设置页都能改。搜索走和风 GeoAPI（`/geo/v2/city/lookup`），区县级都能查到，
  例如 `荆州区` `公安县` `洪湖`；选中后写入的是**该区县的经纬度**，实况/预报/空气质量/预警都用它，
  所以不会退化成「整个市」
- 和风的搜索结果 `name` 不带「区/县」后缀（`公安` = 公安县），页面会把省 / 市拼在右边避免歧义，
  省市区信息另存 `location_admin`
- 设置页也可以完全手填：`城市名` + `位置值`。位置值支持 `经度,纬度`（如 `112.23,30.06`，
  全角逗号也行）或和风 LocationID；填坐标时会自动解析出 `location_lat/lon`，
  这样空气质量与预警（只收经纬度）也能正常拿到

**经纬度顺序**：一律 **经度在前、纬度在后**（`112.23,30.06` = 湖北公安，经度 112.23°E、纬度 30.06°N），
因为和风 API 的 `location` 参数就是这个顺序，直接透传，不做任何换位。
配置里 `location` 存的是 `"经度,纬度"`，而 `location_lat` / `location_lon` 是两个独立字段，
各自语义明确、不会混。设置页的状态行也按「经度 … · 纬度 …」显示；
若手填时把顺序写反（如 `30.06,112.23`），第二个数超过 90°，页面会提示顺序不对。
显示精度：`location` 保留 2 位小数（和风上限），`location_lat/lon` 保留 5 位。

设置页行为：**默认没有任何提醒通道**，点标题右侧「＋ 添加通道」（按钮常驻）从菜单里选
企业微信机器人或 MagicPush —— 菜单**只列通道类型**，添加后再在卡片上勾选它的用途：

| 用途 | 收什么 | 典型场景 |
|---|---|---|
| **正式** | 定时播报、天气预警 | 家里/公司的正式群，只在真正需要提醒时响 |
| **测试** | 只有「消息格式 → 测试推送」 | 单独的测试群，开发期反复调格式不打扰正式群 |
| **正式 + 测试** | 两者都收 | 一个群既当正式群又用来调格式 |

卡片头部的「正式 / 测试」是两个**可同时点亮的胶囊开关**（至少保留一个），
随时切换、立即生效；一个通道既可以是正式也可以是测试。
正式播报只发给勾了「正式」的通道，测试推送只发给勾了「测试」的通道，
改样式、调模板时怎么点「测试推送」都进不了只勾正式的群。

**同类通道可添加多个**（比如多个企业微信群、多个测试群），在卡片顶部的名称框里改名区分，
名称重复时自动加序号（企业微信机器人 2、3…）。卡片内「删除通道」会从列表移除该通道
（已填凭据一并丢弃），**点完即时生效，刷新不会回来**。
删除会在配置里留一个 id 墓碑（`push.removed`，保留最近 32 个）：设置页保存的是
「页面打开时的快照」，不记墓碑的话，删除后紧跟的一次自动保存（或另一个开着的旧页面）
会把它当成「新 id」重新追加，表现就是「删了又出现」。
所有表单改动**失去焦点即自动保存**（写本机 `conf/config.json`，权限 600）；**端口例外**——
改端口后点「重启服务」，端口随整份配置落盘并原地重启生效。
每个通道卡片内有**「测试连通」**按钮：只发一条极简「🧪 连通性测试」消息，
不取天气、不渲染模板，走的是**该通道自己的地址**。
「测试推送」在**消息格式**卡片标题右侧：只发勾了「测试」的通道，一个都没有时会直接提示先勾一个。
服务仅监听 `127.0.0.1`。首次打开设置页会先跳到 `/login` 设置访问密码（见「设置页访问密码」）。

「测试推送」会**连发两条**，两条都走真实模板渲染，收到什么样就是将来真推送的样子：

| 顺序 | 消息 | 数据来源 | 标题前缀 |
|---|---|---|---|
| 1 | 日常播报 | 真实天气（和风优先，无 key 走 Open-Meteo） | `🧪 测试 ·` |
| 2 | 天气预警 | **示例预警**（暴雨橙色，固定样例，不依赖接口） | `🧪 测试 ·` |

「🧪 测试」只出现在标题，正文不重复。城市名同样只出现在标题（模板的 `{city}`），
正文区块不再带城市名——这也是 `{current}` 区块标题现在是「实况」而不是「城市 · 实况」的原因。

「🧪 测试」只加在标题上，正文不再重复；预警那条因为是示例数据，
正文首行会额外标注一句「示例预警，非真实险情」，避免误当真险情。
示例预警**不会写入**预警去重状态，不会影响真实预警的推送。
两条之间间隔 1.2 秒发送，保证接收顺序。

> 企业微信通道没有标题字段，`🧪 测试 ·` 前缀随标题一起出现在**正文首行**；
> MagicPush 收到的是独立标题。

「消息格式」卡片是**左编辑、右预览**：左边改模板（日常播报 / 天气预警两个页签），
右边按当前表单里的配置实时渲染，改字停 0.7 秒自动刷新，也可以点「刷新」手动重来。
预览用的天气数据与首页共用 60 秒缓存，来回预览不会额外消耗和风额度；
多个通道启用时，预览右上角可以在各自风格（纯文本 / Markdown）之间切换。
预览只生成文本，不会真的发出去。

最底部「服务」卡片可以换端口并重启：改完点「重启服务」，服务**原地重启**
（进程 exec 自身，PID 不变），配置、密钥、推送记录都不受影响，页面自动跳到新端口。
端口范围 1024–65535，被占用或非法会给提示、不会把服务弄挂。
端口写在 `conf/config.json` 的 `server.port`，默认 **18770**。

```bash
./serve.sh                  # 用配置里的端口（默认 18770）
./serve.sh --port 9000      # 临时换端口，不写入配置
```

## 设置页访问密码

设置页能看到和风凭据、企业微信 Webhook、MagicPush 令牌，也能触发推送和重启服务，
所以**默认开启鉴权**。服务启动后第一次打开 `/settings` 会 302 到 `/login`：

- 还没设密码 → 显示「首次使用 · 设置访问密码」（至少 6 位），设完自动登录进设置页
- 已设密码 → 输入密码登录，登录状态在该浏览器保持 **7 天**（`auth.session_hours`）
- 设置页右上角「退出登录」立即清 cookie；「访问密码」卡片里可改密码、关闭鉴权

实现在 `auth.py`（纯标准库，无新依赖）：

| 项 | 做法 |
|---|---|
| 密码存储 | PBKDF2-HMAC-SHA256（20 万轮）+ 随机盐，只存哈希与盐，不存明文 |
| 会话 | 无状态 cookie：`base64(payload).HMAC-SHA256`，payload 只带过期时间 |
| 签名密钥 | `conf/config.json` 的 `auth.secret`，服务重启 / 换端口不变 → 不掉线 |
| Cookie | `HttpOnly; SameSite=Strict; Path=/`；内网是 http，**不加 Secure** |
| 防暴力 | 同一 IP 连续 5 次失败锁 60 秒；改密码轮换密钥，其他设备会话立即失效 |
| 防跨站 | 敏感 POST 校验 `Origin` 与 `Host` 一致（curl 无 Origin 时放行，便于命令行调用） |

**哪些接口需要登录**（首页保持公开）：

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

**忘记密码**：编辑 `conf/config.json`，把 `auth.password_hash` 与 `auth.salt` 清空，
重启服务后再打开 `/settings` 就会回到「首次使用」流程重新设置。
（不要删 `auth.secret`，那只是会话签名密钥。）

## 定时播报（唯一载体：网页服务）

**所有定时任务都由 `server.py` 进程内的守护调度线程执行**，在设置页「定时播报」里配置。
项目里没有 1Panel 计划任务、没有 crontab、没有 systemd timer，也不需要你另外去配。

> 服务在运行 → 首页、设置页、日常播报、预警轮询都在；
> 服务停止（或没启动）→ 这些功能一起停止，不会有东西在后台偷偷推送。

| 任务 | 开关 | 触发 | 推送条件 |
|---|---|---|---|
| 日常播报 | 每天定时日常播报 | 北京时间 `HH:MM`，可配多个（最多 12 个） | 到点推一次，同一分钟只推一次 |
| 预警轮询 | 定时预警轮询 | 每 N 分钟（1–120，默认 10） | 只有新预警 / 预警升级才推，无预警零打扰 |

- 两类都**默认关闭**（`schedule.enabled=false`、`schedule.warning_enabled=false`），
  在设置页开启即生效（自动保存），不需要重启服务
- 调度线程每 20 秒检查一次；启动后 45 秒内不触发，避免重启瞬间补播
- `conf/schedule_state.json` 记录上次触发时间：日常播报同一分钟只推一次；
  预警轮询在**服务重启后从当前时刻重新起算**，绝不补推停机期间的旧预警
- 「立即播报一次」/「立即检查预警」按当前配置手动跑一次（带二次确认），用于验证配置
- 播报走 `main.run_forecast()`，预警走 `main.run_warning()`；都会写
  `conf/last_run.json`（首页「最近一次推送」），日志进 `logs/server.log`

服务挂掉是刻意的设计：**不会自动拉起，也不会有任何推送**。要恢复就把服务重新起起来：

```bash
cd "/vol2/1000/agent沙箱/xuechengyun_project/weather_notify"
nohup ./serve.sh >> logs/server.log 2>&1 < /dev/null &
```

如果需要开机自启 / 进程保活，请自行在 1Panel 里守护 `serve.sh` 这个**进程**；
那是进程保活而不是定时任务，服务在功能就在，语义不变。

## 数据源与降级

| | 和风天气 | Open-Meteo（兜底） |
|---|---|---|
| 凭据 | API Host + 项目 ID + JWT 凭据 | 无 |
| 官方灾害预警 | ✅ | ❌ |
| 生活指数 | ✅ | ❌ |
| 空气质量 | 国标 AQI | 美标 US AQI（页面标注） |
| 逐小时 / 逐日 | ✅（逐日 3/7/10/15/30 天） | ✅（最多 16 天） |

降级规则：和风凭据齐全 → 走和风；凭据缺失或请求失败 → 若配置里有坐标则走 Open-Meteo，
并在返回值里带 `fallback_reason` 供页面说明；连坐标都没有才报错。

推送（`main.py`）**不做降级**：没有和风就拿不到预警，宁可不发也不发残缺消息。

> 和风的 `/v7/air/now` 与 `/v7/warning/now` 已于 2025 年废弃（返回 403 Deprecated），
> 本项目改用新版 `/airquality/v1/current/{lat}/{lon}` 与 `/weatheralert/v1/current/{lat}/{lon}`。
> 这两个接口只收经纬度，所以**城市必须是「经度,纬度」或已定位**（IP 自动定位就会写成这种形式）；
> 若城市填的是城市名/LocationID，实况与预报照常，但空气质量与预警会跳过并记日志。

## 空气质量标准

AQI 数值和等级文字**都来自数据接口**，本地不重新算，避免两套阈值打架；本地只用阈值上色。

| | 和风（默认） | Open-Meteo（兜底） |
|---|---|---|
| 标准 | `cn-mee` 中国国标 **HJ 633—2026**（2026-03-01 实施，代替已废止的 HJ 633—2012） | `us-epa` 美国 EPA |
| 选取逻辑 | `qweather.py` 在 `indexes[]` 里优先取 `code` 以 `cn` 开头的那个，没有才退回 `us` | 接口只给 `us_aqi`，`openmeteo.py` 按美标阈值算等级 |
| 等级文字 | 接口返回的 `category`（优/良/轻度污染/中度污染/重度污染/严重污染） | 本地 `_aqi_category()` 按美标断点算 |
| 界面标注 | 空气质量单元格悬停提示「中国国标 HJ 633—2026」 | 悬停提示「美国 EPA 标准」，推送正文里带「（美标）」 |

国标分级（HJ 633—2026；档次与 2012 版相同，修订的是 PM10 / PM2.5 分指数浓度限值）：

| AQI | 级别 | 类别 | 颜色 |
|---|---|---|---|
| 0–50 | 一级 | 优 | 绿 |
| 51–100 | 二级 | 良 | 黄 |
| 101–150 | 三级 | 轻度污染 | 橙 |
| 151–200 | 四级 | 中度污染 | 红 |
| 201–300 | 五级 | 重度污染 | 紫 |
| >300 | 六级 | 严重污染 | 褐红 |

**主页底部**有默认折叠的「空气质量指数 AQI · 分级」卡片，列出上表并附官方原文：

- 标准正文：https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/202602/t20260225_1144441.shtml
- PDF 全文：https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/202602/W020260225366493492011.pdf

主卡「空气质量」值后面的 ⓘ、七天曲线图例里的「AQI 分级 ↗」都能展开并滚到该卡片。

配色统一定义在 `web/style.css` 的 `--aqi-1..--aqi-6`（浅色 / 深色各一套）；
`web/index.html` 的 `AQI_LEVELS` 是六档区间的唯一数据源，主卡 `aqiColor()`、七天表 `aqiClass()`、
底部分级表都读它；推送消息色点在 `formatter.py` 的 `_aqi_mark()`，按同样六档给出。

## 逐小时的百分比

「未来数小时」每列底部的蓝色百分比是**降水概率**（和风 `pop` / Open-Meteo
`precipitation_probability`），表示**该小时出现降水的可能性**，不是降水量、也不是湿度。
卡片标题行右侧常驻「蓝色 % = 降水概率（非降水量）」并配同色圆点；悬停单个百分比
会显示完整说明；推送正文同样写作「降水概率 20%」。页面在概率为 0 时留空（推送仍会写出 0%）。

## IP 自动定位

首次启动时，若 `config.location` 为空，会并发查询多个定位源并写入配置：

| 源 | 作用 |
|---|---|
| ip-api.com | 经纬度 + 中文省名（免费版走 http） |
| ipwho.is | 经纬度 + 英文城市名（备用） |
| myip.ipip.net | 纯中文省市名 |
| whois.pconline.com.cn | 纯中文省市名（GBK） |

合并规则：坐标取优先级最高的可用源，城市显示名优先用中文源（ipip / pconline）。
写入 `location` 为「经度,纬度」（和风支持该格式，最多两位小数），同时记录
`location_source=ip`、`location_ip`、`location_updated`。

⚠️ 定位基于**公网出口 IP**。若机器流量经代理 / VPN（旁路由分流很常见），
不同定位源可能看到不同出口 IP，此时会写入 `location_note` 并在页面上黄色提示，
请核对后到设置页手动改城市。手动改过 `location` 会被标记为 `location_source=manual`。

关闭自动定位：`python3 server.py --no-geoip`。

## 配置

也可直接编辑文件（`./serve.sh` 是推荐方式）：

```
conf/
├── config.json          # 主配置
└── ed25519-private.pem  # 和风 JWT 凭据私钥
```

config.json 字段：

| 字段 | 说明 |
|---|---|
| server.port | 本地网页服务端口（默认 18770，设置页最底部可改，改完点「重启服务」生效） |
| server.host | 监听地址（默认 `127.0.0.1`；NAS 部署写局域网 IP 如 `192.168.1.100`） |
| auth.enabled | 设置页鉴权开关（默认 `true`，见「设置页访问密码」） |
| auth.password_hash / salt | PBKDF2 密码哈希与随机盐，由登录页生成，不要手填 |
| auth.secret | cookie 签名密钥，服务首次启动自动生成，不要外泄 |
| auth.session_hours | 登录保持时长（默认 168 小时 = 7 天） |
| schedule.enabled | 内置定时播报开关（默认 `false`，见「定时播报」） |
| schedule.times | 播报时间点数组，北京时间 `HH:MM`，如 `["07:30","18:00"]` |
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
| push.wecom.enabled / webhook | 企业微信群机器人开关与地址 |
| push.wecom.msg_type | `text`（默认，微信侧不渲染 markdown）/ `markdown`（只看企业微信客户端时更清晰） |
| push.magicpush.enabled / base_url / token | MagicPush 开关、地址与接口令牌 |
| message.broadcast_title / broadcast_body | 日常播报的标题与正文模板 |
| message.alert_title / alert_body | 天气预警的标题与正文模板 |
| hourly_hours | 逐小时预报显示条数（默认 6） |
| forecast_days | 逐日预报天数：3/7/10/15/30（默认 7；超 7 天需和风相应订阅，权限不足自动回落 3 天） |
| indices_types | 生活指数类型（默认 3,5,9 = 穿衣/紫外线/感冒；也可 1 运动 / 2 洗车） |
| alert_min_level | 推送的最低预警等级：白色/蓝色/黄色/橙色/红色（默认蓝色） |
| alert_types | 只推指定类型，如 `["大风","暴雨"]`；空数组 = 全部 |
| alert_resend_hours | 同一条预警重复推送间隔（默认 6 小时，等级升级则立即重推） |

生成和风密钥对：

```bash
openssl genpkey -algorithm ED25519 -out conf/ed25519-private.pem
openssl pkey -pubout -in conf/ed25519-private.pem
# 公钥上传到和风控制台「创建凭据 → JWT」，得到 credential_id
```

## 消息模板

设置页「消息格式」卡片可直接改，失去焦点自动保存即生效，无需重启。模板用花括号占位符拼装：

| 类型 | 占位符 |
|---|---|
| 区块（渲染成整段，没数据时自动留空） | `{warnings}` `{current}` `{air}` `{hourly}` `{daily}` `{indices}` |
| 标量 | `{city}` `{icon}` `{temp}` `{feels}` `{text}` `{wind}` `{humidity}` `{aqi}` `{aqi_category}` `{date}` `{weekday}` `{clock}` `{time}` `{source}` `{alert_type}` `{alert_level}` `{alert_title}` `{alert_status}` |

默认标题（可在设置页改，清空即回到默认）：

```
日常播报  {icon} {city}天气 {date} {weekday} · {temp}°C {text}
天气预警  ⚠️ {city}{alert_type}预警（{alert_level}）· {clock} 发布
```

- `{date}` 今天日期（11月2日）、`{weekday}` 星期（周一）、`{clock}` 当前时刻（19:25），
  三者**按北京时间**计算（与天气数据源的 Asia/Shanghai 一致），机器时区是 UTC 也不会差一天。
- `{time}` 是数据源给出的更新时间，和 `{clock}` 不是一回事，别混用。
- 默认播报正文为 `{warnings}` → `{current}` → `{air}` → `{daily}` → `{hourly}` → `{indices}`，
  想调整顺序或删掉某段，直接改文本即可。
- 升级默认模板时，只有**没被改过**的配置才会跟着变（仍是旧默认值的那条），自己改过的保持原样。
- 写错的占位符会**原样显示**（如 `{tempt}`），便于发现笔误，不会静默丢内容。
- 右侧预览按当前页签渲染：播报用真实天气，预警用**示例预警**（真实预警很少出现，
  这样才调得动格式），预览不推送。
- 同一份模板按通道渲染成两种风格：企业微信选纯文本时去掉 `##`/`**`/`<font>`，
  MagicPush 用 markdown。多个通道启用时，预览右上角出现风格切换，点哪个看哪个。

### 标题在各通道的去向

企业微信群机器人 webhook 的协议只有 `msgtype` + `content`，**没有 title 字段**
（MagicPush 是 `{title, content}` 两个字段），所以标题只能自己拼进正文：

| 通道 | 标题去哪了 | 线上效果 |
|---|---|---|
| 企业微信（markdown） | 正文首行，渲染成 `# 标题` | 手机/PC 上都显示为大标题 |
| 企业微信（纯文本） | 正文首行，就是一行普通文字 | 微信侧收到时第一行即标题 |
| MagicPush | 独立 `title` 字段 | 通知栏标题 |

逻辑在 `formatter.inline_title()`，由 `main.push_all()` 对企业微信通道调用；
设置页预览也按同样规则合并（标题区改为提示「标题已并入正文首行」），
**预览看到的就是实际收到的**，不会出现「预览有标题、手机上没有」的落差。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 仅 requests + cryptography
.venv/bin/python main.py           # 单次调试：播一次日常播报
.venv/bin/python main.py --warning # 单次调试：检查一次预警
./serve.sh                         # 启动服务（首页 + 登录 + 设置 + 全部定时任务）
./serve.sh --host 192.168.1.100     # 允许局域网访问（写具体网卡 IP）
```

`serve.sh` 启动后，浏览器打开 `/settings` 会先要求设置访问密码（见「设置页访问密码」）。
`main.py` 是单次 CLI 调试命令，不经过 Web 服务、不受鉴权影响，也不参与任何定时调度；
定时任务只有 `serve.sh` 这一个进程在跑。

## 部署到 NAS / 局域网访问

服务**默认只监听 `127.0.0.1`**（`config_loader.DEFAULT_HOST`）。部署在 NAS 上时，
从别的机器访问必须改监听地址，下面两种方式任选其一：

| 方式 | 做法 | 重启后是否保留 |
|---|---|---|
| 配置（推荐） | `conf/config.json` 的 `server.host` 设为 NAS 的局域网 IP（如 `192.168.1.100`） | 是 |
| 命令行 | `./serve.sh --host 192.168.1.100` 或 `python server.py --host 192.168.1.100` | 是（重启会重放启动参数） |

**建议写具体网卡 IP，而不是 `0.0.0.0`**：`0.0.0.0` 会监听所有网卡，
在装了 Tailscale / OpenVPN / Docker 的 NAS 上等于把设置页一并开放到
`100.64.x`、`10.8.x`、`172.x` 等网段，通常没必要。
日志会直接打印可点击的 URL；设置页「当前地址」则按浏览器实际使用的主机名显示。
不要再靠「本机出口 IP」去猜地址——有旁路由时路由表选出的可能是 `198.18.x` 这类虚拟地址。

```bash
# 以 fnOS NAS 为例，项目放在 /vol2/1000/agent沙箱/xuechengyun_project/weather_notify
cd "/vol2/1000/agent沙箱/xuechengyun_project/weather_notify"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 确认 conf/ 下有 config.json 和 ed25519-private.pem（含凭据，权限 600）
.venv/bin/python server.py --host 192.168.1.100   # 或写进 conf/config.json 后直接 ./serve.sh
# 浏览器打开 http://192.168.1.100:18770/settings
```

> ✅ 设置页**默认开启访问密码鉴权**（见「设置页访问密码」）：第一次打开
> `http://192.168.1.100:18770/settings` 会跳到 `/login`，设置密码后才进得去；
> 首页天气仍可公开查看。绑到局域网 IP 仍建议只在可信网段内使用、别用弱口令；
> 如只需临时访问，可保持 `127.0.0.1` 并用 SSH 端口转发
> （`ssh -L 18770:127.0.0.1:18770 user@nas`）。

定时任务**不需要在这里额外配置**：只要 `serve.sh` 这个进程在跑，设置页里开启的
日常播报与预警轮询就会按点执行；进程一停就全部停止。详见「定时播报」。

## 接口

| 方法 | 路径 | 登录 | 说明 |
|---|---|---|---|
| GET | `/`、`/login` | — | 首页天气面板 / 登录页 |
| GET | `/api/weather` | — | 结构化天气 JSON（60 秒缓存；`?fresh=1` 强制刷新有 15 秒全局限流） |
| GET | `/api/status` | — | 配置状态：缺项、通道、位置、最近一次推送 |
| GET | `/api/auth/status` | — | 鉴权状态：开关、是否已设密码、是否已登录、会话时长 |
| GET | `/api/state` | ✅ | 原始配置（`auth` 段脱敏）+ 状态（设置页用） |
| GET | `/api/schedule` | ✅ | 调度状态：播报时间点、下次/上次触发、预警轮询间隔与下次检查 |
| POST | `/api/auth/setup` | — | 首次设置访问密码（仅未设密码时可用），成功后下发 cookie |
| POST | `/api/auth/login` | — | 校验密码并下发 cookie（同一 IP 连续 5 次失败锁 60 秒） |
| POST | `/api/auth/logout` | ✅ | 清除 cookie |
| POST | `/api/auth/password` | ✅ | 改密码（校验原密码），轮换签名密钥让其他设备掉线 |
| POST | `/api/auth/toggle` | ✅ | 开关设置页鉴权 |
| POST | `/api/config` | ✅ | 保存配置 |
| POST | `/api/geoip` | — | 按公网 IP 定位；只有 `apply=true` 才写配置（首页 / 设置页都显式传） |
| POST | `/api/city` | — | 按名称搜城市（body `{keyword}`，可带 `config`），返回省 / 市 / 区县候选 |
| POST | `/api/location` | — | 直接用候选城市替换位置（body 为 `/api/city` 返回的一项），立即生效 |
| POST | `/api/message` | ✅ | 生成推送消息预览（不推送）。`mode=broadcast`（默认）用真实天气；`mode=alert` 用示例预警 |
| POST | `/api/test` | ✅ | 测试推送：**连发两条** —— 日常播报（真实天气）+ 天气预警（示例预警），都带「🧪 测试」前缀；**只发勾了「测试」用途的通道**（`roles` 含 `test`），只勾正式的通道一条都不碰；不要求和风凭据（无 key 时走 Open-Meteo）。设置页「消息格式 → 测试推送」按钮用 |
| POST | `/api/test-channel` | ✅ | 单通道连通性测试：只发一条极简「🧪 连通性测试」消息（不取天气、不渲染模板），走该通道自己的地址。body `{id, config}`，按通道 id 匹配（优先用页面提交的凭据，未保存也能测）。设置页各通道卡片的「测试连通」按钮用 |
| POST | `/api/test-qweather` | ✅ | 和风 API 连通性测试：真实调用一次 GeoAPI，同时验证 API Host、项目 ID、凭据 ID 与 Ed25519 私钥。body `{config}` 优先用页面提交的未保存凭据。设置页「天气数据」卡片的「测试 API」按钮用 |
| POST | `/api/channel/add` | ✅ | 添加通道实例：body `{type, roles?}`（`type`: `wecom` \| `magicpush`；`roles` 可选，默认 `["prod"]`），往 `push.channels` 追加一项并默认启用，同类可加多个、名称自动去重；用途（正式/测试，可同时勾）添加后在卡片上改 |
| POST | `/api/channel/remove` | ✅ | 删除通道实例：body `{id}`，从 `push.channels` 移除（凭据一并丢弃），并把 id 记进删除墓碑 `push.removed`，防止旧快照保存时把通道写回 |
| POST | `/api/run` | ✅ | 立即播报（拉天气 + 推送）。设置页「定时播报 → 立即播报一次」有按钮，也可 curl 触发 |
| POST | `/api/run-warning` | ✅ | 立即检查一次预警（只有新预警才推送），设置页「立即检查预警」按钮用 |
| POST | `/api/restart` | ✅ | 保存端口并原地重启服务（body 可带 `config`，缺省只改端口） |

> 标 ✅ 的接口和 `/settings` 需要登录，未登录返回 401（页面跳 `/login`）；
> 敏感 POST 还会校验 `Origin` 与 `Host` 一致，跨站调用返回 403。

> 定时任务只由本服务的调度线程触发；「立即播报一次」「立即检查预警」按钮带二次确认，
> 避免误点推到所有通道。命令行手动补一次要先登录拿 cookie：
> `curl -c /tmp/wn.cookie -X POST -H 'Content-Type: application/json' -d '{"password":"你的密码"}' http://127.0.0.1:18770/api/auth/login`
> 然后 `curl -b /tmp/wn.cookie -X POST http://127.0.0.1:18770/api/run`
> （部署到 NAS 且绑定了具体网卡 IP 时，把 `127.0.0.1` 换成 NAS 的 IP）

## 文件结构

| 文件 | 职责 |
|---|---|
| `main.py` | 数据入口 `fetch_weather()`（双源切换）、播报流程、预警去重、运行记录 |
| `server.py` | 本地 Web 服务（首页 / 登录 / 设置 / API / 静态资源）、鉴权路由、内置定时调度 |
| `auth.py` | 设置页鉴权：PBKDF2 密码哈希、HMAC 签名 cookie、登录失败限流 |
| `geoip.py` | 公网 IP 定位（多源并发 + 中文名优先 + 出口 IP 警示） |
| `openmeteo.py` | Open-Meteo 兜底源（WMO 代码 → 中文/图标、蒲福风级） |
| `qweather.py` | 和风客户端（JWT 签发 + weather/indices 走 v7，airquality/weatheralert 走 v1） |
| `formatter.py` | 消息组装：模板占位符 + md / 纯文本两种风格，预警置顶 |
| `pusher.py` | 企业微信 webhook + MagicPush |
| `config_loader.py` | 配置读取/合并/校验、定位写盘 |
| `web/style.css` | 共享设计系统（三页共用，含深色模式） |
| `web/index.html` | 首页（天气面板） |
| `web/login.html` | 登录页（首次设置密码 / 输入密码） |
| `web/settings.html` | 设置页 |

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

标准库 `unittest`，零新依赖（NAS 上同样可跑），覆盖：

| 文件 | 内容 |
|---|---|
| `test_config_loader.py` | 时间点归一化、间隔钳制、坐标解析、配置迁移、密钥脱敏 |
| `test_auth.py` | 密码哈希校验、cookie 签名与过期、登录失败限流 |
| `test_formatter.py` | AQI 色点、星期换算、模板渲染、标题并入正文 |
| `test_server_schedule.py` | 调度触发/去重/宽限期、预警轮询间隔、表单清洗 |
| `test_main.py` | 坐标解析、预警过滤去重（等级/类型/升级/复推窗口）、运行记录 |
| `test_qweather.py` | 新版预警结构归一化（颜色等级、解除通知、字段兜底） |

所有用例把 `conf/` 指到临时目录，**不会读写真实配置**；调度与推送线程通过 mock
隔离，测试期间不会真的发消息、不会调用天气接口。

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

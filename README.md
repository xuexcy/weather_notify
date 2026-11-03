# weather-notify

> 项目由 AI 生成。

和风天气 → 企业微信机器人 / MagicPush 天气提醒，含官方灾害预警。一个进程跑完所有事：
网页看图、设置凭据、定时播报、预警轮询。

由 [cuijianzhuang/weather_push](https://github.com/cuijianzhuang/weather_push) 裁剪重写。

## 特性

- **和风天气新版认证**：JWT（Ed25519），密钥在设置页一键生成 / 替换，不用 devapi + key 老方式
- **官方灾害预警**（暴雨 / 台风 / 大风 / 寒潮等），有预警时预警置顶推送
- **日常播报**：实况 + 空气质量 + 逐日 + 逐小时 + 生活指数
- **无和风凭据也能用**：自动降级 Open-Meteo（无需注册），首页照样有天气，只是没有官方预警
- **网页界面**：首页看天气 / 设置页填凭据，提醒通道可添加多个实例（含同类多个）
- **通道用途可选**：每个通道能勾「正式」「测试」（可同时勾），调格式不打扰正式群
- **设置页访问密码**：PBKDF2 密码哈希 + HMAC 签名 cookie，首页天气仍可公开查看
- **消息模板可改**：日常播报与天气预警各有标题 / 正文模板，用 `{变量}` 拼装
- **私密数据隔离**：凭据与私钥全在 `conf/`（已 gitignore，权限 600）
- **首次启动按公网 IP 自动定位城市**，无需自己查 LocationID

## 快速开始

需要 Python **3.10+**（本地或 NAS 均可，纯标准库 + 两个依赖）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 仅 requests + cryptography
./serve.sh                                  # 默认 http://127.0.0.1:18770
```

打开 `http://127.0.0.1:18770` 就是天气首页；点右上角「设置」会先跳到 `/login`
**让你设一个访问密码**，设完自动进入设置页。

> 想先看效果不配凭据也行：没有和风凭据时自动走 Open-Meteo，首页照样有天气（没有官方预警）。

### 飞牛 fnOS 用户

可以装成系统应用（桌面图标、应用中心启停/升级，配置在应用数据目录）：
在 fnOS 上执行 `./build-fpk.sh --arch both` 生成 fpk（x86_64 与 ARM64 各一个），
应用中心手动安装即可，详见 [docs/packaging.md](docs/packaging.md)。

## 使用方法

设置页从上到下就是配置顺序，改动**失去焦点即自动保存**，无需重启。

**1. 天气数据** — 填和风 API Host、项目 ID、凭据 ID，然后点密钥区的**「生成密钥对」**，
把显示出来的公钥粘贴到和风控制台「创建凭据 → JWT」拿到凭据 ID。也可以点
**「替换私钥」**粘贴自己已有的私钥（PEM 或 ssh-keygen 生成），公钥会自动推导。
填完点卡片右上角**「测试 API」**，会真实调一次接口，同时验证这四项是否配套。

**2. 城市位置** — 三种方式任选：搜城市名（可精确到区县）、点「按 IP 定位」、或手填
`经度,纬度`。位置必须是「经度,纬度」或已定位，空气质量与预警才拿得到。

**3. 提醒通道** — 点「＋ 添加通道」选类型（企业微信机器人 / MagicPush），填凭据后
在卡片头勾选用途：

| 用途 | 收什么 |
|---|---|
| 正式 | 定时播报、天气预警 |
| 测试 | 只有「消息格式 → 测试推送」 |
| 正式 + 测试 | 两者都收 |

同类通道可以加多个（多个群、多个测试群），卡片里改名区分即可。填完先点
**「测试连通」**确认地址通不通，再去调格式。

**4. 定时播报** — 在「定时播报」卡片开开关：日常播报填时间点（北京时间，最多 12 个），
预警轮询填间隔分钟数。**默认都是关的**，打开即生效。

**5. 调格式** — 「消息格式」卡片左改模板右看预览，改字停 0.7 秒自动刷新。
点标题右侧**「测试推送」**会把两条真实渲染的消息（日常播报 + 示例预警）发给勾了「测试」的通道，
正式群一条都不会收到。

详细说明：界面行为见 [docs/ui.md](docs/ui.md)，模板语法见
[docs/message-template.md](docs/message-template.md)。

## 命令行调试

```bash
.venv/bin/python src/main.py            # 单次播报（不经过 Web 服务，不受鉴权影响）
.venv/bin/python src/main.py --warning  # 单次检查预警，只有新预警才推送
./serve.sh --port 9000                  # 临时换端口
./serve.sh --host 192.168.1.100         # 允许局域网访问（写具体网卡 IP）
```

`src/main.py` 只作手动调试，不参与定时；定时任务只有服务进程在跑。

## 目录结构

```
src/          后端代码（唯一进程 server.py + 天气/推送/配置等模块）
web/          前端三页：首页、登录、设置（共享 style.css）
tests/        单元测试（标准库 unittest，182 例）
docs/         实现细节与参考文档
fpk/          飞牛 fnOS 应用包骨架（manifest、生命周期脚本、图标）
build-fpk.sh  打包脚本：./build-fpk.sh [版本号] → dist/*.fpk
conf/         本地配置与私钥（gitignore，不入库）
logs/         运行日志（gitignore）
```

| 文件 | 职责 |
|---|---|
| `src/main.py` | 数据入口 `fetch_weather()`（双源切换）、播报流程、预警去重、运行记录 |
| `src/server.py` | Web 服务（首页 / 登录 / 设置 / API / 静态资源）、鉴权路由、内置定时调度 |
| `src/auth.py` | 设置页鉴权：PBKDF2 密码哈希、HMAC 签名 cookie、登录失败限流 |
| `src/qweather.py` | 和风客户端（JWT 签发 + weather/indices 走 v7，airquality/weatheralert 走 v1） |
| `src/openmeteo.py` | Open-Meteo 兜底源（WMO 代码 → 中文 / 图标、蒲福风级） |
| `src/geoip.py` | 公网 IP 定位（多源并发 + 中文名优先 + 出口 IP 警示） |
| `src/formatter.py` | 消息组装：模板占位符 + md / 纯文本两种风格，预警置顶 |
| `src/pusher.py` | 企业微信 webhook + MagicPush |
| `src/config_loader.py` | 配置读取 / 合并 / 校验 / 迁移、通道增删、定位写盘 |
| `web/index.html` | 首页（天气面板） |
| `web/settings.html` | 设置页 |
| `web/login.html` | 登录页（首次设密码 / 输入密码） |

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ui.md](docs/ui.md) | 网页界面：首页、设置页、城市位置与经纬度、测试推送 |
| [docs/configuration.md](docs/configuration.md) | 配置字段全表、通道结构、和风密钥生成 |
| [docs/data-sources.md](docs/data-sources.md) | 和风 vs Open-Meteo 降级、废弃接口、IP 定位 |
| [docs/air-quality.md](docs/air-quality.md) | AQI 标准 HJ 633—2026 与六档分级、逐小时降水概率 |
| [docs/message-template.md](docs/message-template.md) | 模板占位符、默认模板、标题在各通道的去向 |
| [docs/scheduling.md](docs/scheduling.md) | 定时播报 / 预警轮询的触发、去重、状态文件 |
| [docs/auth.md](docs/auth.md) | 访问密码实现、受保护接口清单 |
| [docs/api.md](docs/api.md) | HTTP 接口一览、curl 示例 |
| [docs/deployment.md](docs/deployment.md) | NAS / 局域网部署、服务启停、git 更新 |
| [docs/packaging.md](docs/packaging.md) | 打包飞牛 fnOS 应用（fpk）：构建、安装、数据目录、排查 |

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

标准库 `unittest`，零新依赖，NAS 上同样可跑。所有用例把 `conf/` 指到临时目录，
**不会读写真实配置**；调度与推送线程通过 mock 隔离，测试期间不会真的发消息、
不会调用天气接口。

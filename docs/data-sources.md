# 数据源与降级

## 和风 vs Open-Meteo

| | 和风天气 | Open-Meteo（兜底） |
|---|---|---|
| 凭据 | API Host + 项目 ID + JWT 凭据 | 无 |
| 官方灾害预警 | ✅ | ❌ |
| 生活指数 | ✅ | ❌ |
| 空气质量 | 国标 AQI | 美标 US AQI（页面标注） |
| 逐小时 / 逐日 | ✅（逐日 3/7/10/15/30 天） | ✅（最多 16 天） |

降级规则：和风凭据齐全 → 走和风；凭据缺失或请求失败 → 若配置里有坐标则走 Open-Meteo，
并在返回值里带 `fallback_reason` 供页面说明；连坐标都没有才报错。

推送（`src/main.py`）**不做降级**：没有和风就拿不到官方预警，宁可不发也不发残缺消息。

> 和风的 `/v7/air/now` 与 `/v7/warning/now` 已于 2025 年废弃（返回 403 Deprecated），
> 本项目改用新版 `/airquality/v1/current/{lat}/{lon}` 与 `/weatheralert/v1/current/{lat}/{lon}`。
> 这两个接口只收经纬度，所以**城市必须是「经度,纬度」或已定位**（IP 自动定位就会写成这种形式）；
> 若城市填的是城市名 / LocationID，实况与预报照常，但空气质量与预警会跳过并记日志。

AQI 标准与分级细节见 [air-quality.md](air-quality.md)。

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

关闭自动定位：`python3 src/server.py --no-geoip`。

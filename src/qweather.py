"""和风天气 API 客户端（新版 JWT / EdDSA 认证，专属 API Host）。

认证方式：控制台创建「JWT 凭据」（Ed25519 密钥对），
请求头携带 Authorization: Bearer <JWT>，不再使用 devapi.qweather.com + key 老方式。
"""
import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

log = logging.getLogger("weather-notify.qweather")

# 和风逐日预报只有这几档（按订阅权限开放）
FORECAST_STEPS = (3, 7, 10, 15, 30)
BEIJING = timezone(timedelta(hours=8))


class QWeatherError(Exception):
    pass


def _pick_days(days) -> int:
    """把任意天数收敛到和风支持的档位：就近向上取，超过 30 按 30。"""
    try:
        n = int(days)
    except (TypeError, ValueError):
        return 7
    for step in FORECAST_STEPS:
        if n <= step:
            return step
    return FORECAST_STEPS[-1]


def _local_date(iso: str) -> str:
    """UTC 时间戳 → 北京时间日期串（逐日空气质量用 UTC 日界，需换算）。"""
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class QWeatherClient:
    def __init__(self, api_host: str, project_id: str, credential_id: str,
                 private_key_pem: str, timeout: int = 10):
        self.api_host = api_host.rstrip("/")
        self.project_id = project_id
        self.credential_id = credential_id
        self.timeout = timeout
        try:
            key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        except Exception as e:
            raise QWeatherError(f"加载 Ed25519 私钥失败: {e}")
        if not isinstance(key, Ed25519PrivateKey):
            raise QWeatherError("私钥类型错误：和风 JWT 凭据要求 Ed25519 密钥对")
        self._key = key
        self._jwt = ""
        self._jwt_exp = 0

    def _make_jwt(self) -> str:
        header = {"alg": "EdDSA", "kid": self.credential_id}
        now = int(time.time())
        payload = {"sub": self.project_id, "iat": now, "exp": now + 900}
        h = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h}.{p}"
        sig = self._key.sign(signing_input.encode())
        return f"{signing_input}.{_b64url(sig)}"

    def _token(self) -> str:
        if not self._jwt or time.time() > self._jwt_exp - 60:
            self._jwt = self._make_jwt()
            self._jwt_exp = time.time() + 900
        return self._jwt

    def _get(self, path: str, params: dict, legacy: bool = True) -> dict:
        """legacy=True 的老接口（/v7/*）用 body 里的 code 判成败；
        新版 /v1/* 没有 code 字段，只看 HTTP 状态。"""
        url = f"https://{self.api_host}{path}"
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise QWeatherError(f"请求 {path} 网络异常: {e}")
        try:
            data = resp.json()
        except ValueError:
            raise QWeatherError(f"请求 {path} 返回非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}")
        if not legacy:
            if resp.status_code >= 400:
                raise QWeatherError(f"请求 {path} 失败: HTTP {resp.status_code} {str(data)[:200]}")
            return data
        code = str(data.get("code", ""))
        # 204 = 无数据（如无预警、无空气质量），属正常
        if code not in ("200", "204"):
            raise QWeatherError(f"请求 {path} 失败: code={code} {data.get('error', '')}")
        return data

    # ---- 各 API 封装 ----

    def weather_now(self, location: str) -> dict | None:
        data = self._get("/v7/weather/now", {"location": location})
        return data.get("now") if code_is(data, "200") else None

    def weather_24h(self, location: str) -> list:
        data = self._get("/v7/weather/24h", {"location": location})
        if not code_is(data, "200"):
            return []
        return data.get("hourly", [])

    def weather_days(self, location: str, days: int = 7) -> tuple:
        """逐日预报，返回 (daily 列表, 实际拿到的天数)。

        和风按订阅开放 /v7/weather/{3,7,10,15,30}d：高级接口没权限时返回非 200，
        此时退回 3d，保证首页至少有三天可看。
        """
        want = _pick_days(days)
        try:
            data = self._get(f"/v7/weather/{want}d", {"location": location})
            daily = data.get("daily", []) if code_is(data, "200") else []
        except QWeatherError:
            daily = []
        if daily or want == 3:
            return daily, len(daily)
        log.warning("%dd 逐日预报不可用（订阅权限不足？），退回 3d", want)
        data = self._get("/v7/weather/3d", {"location": location})
        daily = data.get("daily", []) if code_is(data, "200") else []
        return daily, len(daily)

    def air_daily(self, lat: float, lon: float) -> dict:
        """逐日空气质量预报，返回 {date: {aqi, category}}。

        该接口固定只给 3 天（无天数参数），超出部分由调用方留空。
        """
        try:
            data = self._get(f"/airquality/v1/daily/{lat:.2f}/{lon:.2f}", {},
                             legacy=False)
        except QWeatherError as e:
            log.warning("逐日空气质量获取失败: %s", e)
            return {}
        out = {}
        for day in data.get("days") or []:
            date = _local_date(day.get("forecastStartTime"))
            if not date:
                continue
            indexes = day.get("indexes") or []
            cn = next((i for i in indexes if str(i.get("code", "")).startswith("cn")),
                      None)
            pick = cn or (indexes[0] if indexes else None)
            if not pick:
                continue
            out[date] = {"aqi": _num(pick.get("aqi")),
                         "category": pick.get("category") or ""}
        return out

    def air_now(self, lat: float, lon: float) -> dict | None:
        """新版空气质量（/v7/air/now 已废弃）。

        /airquality/v1/current/{lat}/{lon} 返回多套标准（cn-mee 国标、us-epa 美标），
        优先取国标，没有国标才退回美标并在 std 里标明。
        """
        data = self._get(f"/airquality/v1/current/{lat:.2f}/{lon:.2f}", {}, legacy=False)
        indexes = data.get("indexes") or []
        if not indexes:
            return None
        cn = next((i for i in indexes if str(i.get("code", "")).startswith("cn")), None)
        us = next((i for i in indexes if str(i.get("code", "")).startswith("us")), None)
        pick, std = (cn, "CN") if cn else (us, "US")
        if not pick:
            return None
        pol = {}
        for item in data.get("pollutants") or []:
            value = (item.get("concentration") or {}).get("value")
            if value is not None:
                pol[str(item.get("code", ""))] = _num(value)
        return {
            "aqi": _num(pick.get("aqi")),
            "category": pick.get("category") or "",
            "std": std,
            "pm2p5": pol.get("pm2p5"),
            "pm10": pol.get("pm10"),
        }

    def indices_1d(self, location: str, types: str) -> dict:
        """types 如 '1,2,3,5,9'（运动/洗车/穿衣/紫外线/感冒）。返回 {type: {...}}"""
        data = self._get("/v7/indices/1d", {"location": location, "type": types})
        if not code_is(data, "200"):
            return {}
        return {item["type"]: item for item in data.get("daily", [])}

    def warning_now(self, lat: float, lon: float) -> list:
        """新版天气预警（/v7/warning/now 已废弃）。

        /weatheralert/v1/current/{lat}/{lon} 返回 alerts[]，字段是国际 CAP 风格；
        这里归一化成页面与 formatter 用的旧结构（typeName / level / title / status / text）。
        """
        data = self._get(f"/weatheralert/v1/current/{lat:.2f}/{lon:.2f}",
                         {"localTime": "true"}, legacy=False)
        return [_alert_out(a) for a in data.get("alerts") or []]

    def city_lookup(self, keyword: str, number: int = 15) -> list:
        """GeoAPI 城市搜索（省/市/区县/乡镇级都能查到）。

        返回 [{id, name, adm1, adm2, country, lat, lon}]，按接口相关度排序。
        注意 GeoAPI 的 name 不带行政区后缀（「公安」= 公安县、「沙市」= 沙市区），
        展示时要拼上 adm1/adm2 才不歧义。
        """
        data = self._get("/geo/v2/city/lookup",
                         {"location": keyword, "number": number, "lang": "zh"})
        if not code_is(data, "200"):
            return []
        out = []
        for item in data.get("location") or []:
            # 坐标不能用 _num（它会压成一位小数），县级定位需要 5 位精度
            try:
                lat = round(float(item.get("lat")), 5)
                lon = round(float(item.get("lon")), 5)
            except (TypeError, ValueError):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            out.append({
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "adm1": str(item.get("adm1") or ""),
                "adm2": str(item.get("adm2") or ""),
                "country": str(item.get("country") or ""),
                "lat": lat,
                "lon": lon,
            })
        return [x for x in out if x["name"]]


def code_is(data: dict, code: str) -> bool:
    return str(data.get("code", "")) == code


def _num(value):
    """数值归一：整数值的浮点转成 int（46.0 → 46）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else round(f, 1)


# 新版预警的严重程度用颜色表达（中国用蓝黄橙红），severity 仅作兜底
COLOR_LEVEL = {"white": "白色", "blue": "蓝色", "green": "绿色", "yellow": "黄色",
               "orange": "橙色", "red": "红色", "black": "黑色"}
SEVERITY_LEVEL = {"minor": "蓝色", "moderate": "黄色", "severe": "橙色", "extreme": "红色"}
MESSAGE_STATUS = {"alert": "预警中", "update": "已更新", "cancel": "已取消"}


def _alert_out(alert: dict) -> dict:
    """新版 alerts[] → 页面与 formatter 使用的结构。"""
    color = str((alert.get("color") or {}).get("code") or "").lower()
    severity = str(alert.get("severity") or "").lower()
    level = COLOR_LEVEL.get(color) or SEVERITY_LEVEL.get(severity, "白色")
    msg = str((alert.get("messageType") or {}).get("code") or "alert").lower()
    if msg == "cancel":
        # 解除通知沿用的是原预警的等级色，直接拿来渲染会变成「红色·已取消」，
        # 又误导又是噪音；按白色处理，默认低于推送门槛
        level = "白色"

    desc = (alert.get("description") or "").strip()
    instruction = (alert.get("instruction") or "").strip()
    if instruction and instruction not in desc:
        desc = f"{desc}\n{instruction}".strip()

    return {
        "id": alert.get("id") or "",
        "typeName": (alert.get("eventType") or {}).get("name") or "预警",
        "level": level,
        "title": alert.get("headline") or "",
        "status": MESSAGE_STATUS.get(msg, msg),
        "text": desc,
        "sender": alert.get("senderName") or "",
        "startTime": (alert.get("onsetTime") or alert.get("effectiveTime")
                      or alert.get("issuedTime") or ""),
    }

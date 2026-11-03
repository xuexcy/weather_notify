"""按公网 IP 推断所在城市（无需任何 API Key）。

用途：服务首次启动时自动填好城市，省去用户去查和风 LocationID。

实现要点：
1. 多源**并发**查询，单个源 6 秒超时，互不阻塞；
2. 坐标与「中文城市名」分别取最优源：坐标优先 ip-api / ipwho.is，
   中文名优先 ipip / pconline（ip-api 的 city 常是拼音）；
3. 若多个源看到的出口 IP 不一致（代理分流），或归属地不在中国大陆，
   会返回 warn 文案 —— 因为此时定位必然不可靠，交由用户核对。

注意：QWeather 需要「经度,纬度」或 LocationID，所以必须有坐标才算成功。
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

log = logging.getLogger("weather-notify.geoip")

TIMEOUT = 6
HEADERS = {
    "User-Agent": "Mozilla/5.0 (weather-notify/1.0)",
    "Accept": "application/json",
}
ZH_RE = re.compile(r"[\u4e00-\u9fff]")


def _get(url: str, encoding: str | None = None) -> dict:
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    if encoding:
        return json.loads(resp.content.decode(encoding, errors="replace"))
    return resp.json()


# ---------------- 各定位源 ----------------

def _p_ip_api() -> dict | None:
    """ip-api.com：坐标 + 中文省名（免费版仅 http）。"""
    d = _get("http://ip-api.com/json/?lang=zh-CN"
             "&fields=status,country,regionName,city,lat,lon,query,isp")
    if d.get("status") != "success":
        return None
    return {"ip": d.get("query", ""), "city": d.get("city", ""),
            "region": d.get("regionName", ""), "country": d.get("country", ""),
            "isp": d.get("isp", ""), "lat": float(d["lat"]), "lon": float(d["lon"])}


def _p_ipwho() -> dict | None:
    """ipwho.is：坐标 + 英文城市名。"""
    d = _get("https://ipwho.is/")
    if not d.get("success", False):
        return None
    return {"ip": d.get("ip", ""), "city": d.get("city", ""),
            "region": d.get("region", ""),
            "country": "中国" if d.get("country_code") == "CN" else d.get("country", ""),
            "isp": d.get("connection", {}).get("isp", ""),
            "lat": float(d["latitude"]), "lon": float(d["longitude"])}


def _p_ipip() -> dict | None:
    """myip.ipip.net：纯中文省市，无坐标。"""
    d = _get("https://myip.ipip.net/json")
    if d.get("ret") != "ok":
        return None
    data = d.get("data") or {}
    loc = data.get("location") or []
    if len(loc) < 3:
        return None
    return {"ip": data.get("ip", ""), "country": loc[0], "region": loc[1],
            "city": loc[2], "isp": loc[4] if len(loc) > 4 else "",
            "lat": None, "lon": None}


def _p_pconline() -> dict | None:
    """太平洋电脑网：纯中文省市，GBK 编码，无坐标。"""
    d = _get("https://whois.pconline.com.cn/ipJson.jsp?json=true", encoding="gbk")
    if d.get("err") or not (d.get("city") or d.get("pro")):
        return None
    return {"ip": d.get("ip", ""), "country": "中国", "region": d.get("pro", ""),
            "city": d.get("city", ""), "isp": "", "lat": None, "lon": None}


# 顺序即优先级：坐标源在前，纯中文名源在后
PROVIDERS = (
    ("ip-api.com", _p_ip_api),
    ("ipwho.is", _p_ipwho),
    ("ipip.net", _p_ipip),
    ("pconline", _p_pconline),
)
_ORDER = {name: i for i, (name, _) in enumerate(PROVIDERS)}


def _clean_city(name: str) -> str:
    """「北京市」→「北京」；「香港特别行政区」→「香港」。"""
    name = (name or "").strip()
    if not name:
        return ""
    for suffix in ("特别行政区", "市"):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


def _is_cn(country: str) -> bool:
    return (country or "").strip() in ("中国", "China", "CN", "中国香港", "中国澳门", "中国台湾")


def _zh_name(pool: list, ip: str) -> tuple[str, str, str] | None:
    """从同 IP 的结果里挑中文城市名。"""
    same = [r for r in pool if not ip or not r.get("ip") or r["ip"] == ip]
    for r in same:
        if r.get("city") and ZH_RE.search(r["city"]):
            return r["city"], r.get("region", ""), r.get("country", "")
    for r in same:
        if r.get("region") and ZH_RE.search(r["region"]):
            return r.get("city") or r["region"], r["region"], r.get("country", "")
    return None


def detect() -> dict:
    """并发查询各源并合并结果。"""
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = {pool.submit(fn): name for name, fn in PROVIDERS}
        for fut in as_completed(futures, timeout=TIMEOUT + 4):
            name = futures[fut]
            try:
                item = fut.result()
            except Exception as e:  # 网络/解析异常都不影响其他源
                failures.append(f"{name} 失败: {e}")
                log.info("IP 定位源 %s 不可用: %s", name, e)
                continue
            if item:
                item["source"] = name
                results.append(item)
                log.info("IP 定位源 %s: %s %s %s", name, item.get("ip"),
                         item.get("city"), item.get("region"))
            else:
                failures.append(f"{name} 无有效结果")

    coords = [r for r in results if r.get("lat") is not None and r.get("lon") is not None]
    if not coords:
        log.warning("IP 定位失败：%s", "; ".join(failures))
        return {"ok": False, "error": "所有 IP 定位源均未返回可用坐标（可能无外网或代理异常）",
                "failures": failures}

    coords.sort(key=lambda r: _ORDER.get(r["source"], 99))
    primary = coords[0]
    ip = primary.get("ip", "")
    country = primary.get("country", "")

    city, region, country_zh = _clean_city(primary.get("city", "")), primary.get("region", ""), country
    zh = _zh_name(results, ip)
    if zh:
        city, region, country_zh = _clean_city(zh[0]), zh[1] or region, zh[2] or country

    ips = sorted({r["ip"] for r in results if r.get("ip")})
    warn = ""
    if len(ips) > 1:
        warn = "检测到多个出口 IP（" + "、".join(ips) + "），可能由代理分流，定位结果仅供参考"
        log.warning(warn)
    elif not _is_cn(country_zh):
        warn = f"出口 IP 归属「{country_zh or '未知'}」而非中国大陆，定位结果仅供参考"
        log.warning(warn)

    return {
        "ok": True,
        "city": city or region or "未知",
        "city_raw": primary.get("city", ""),
        "region": region,
        "country": country_zh,
        "lat": round(float(primary["lat"]), 4),
        "lon": round(float(primary["lon"]), 4),
        "ip": ip,
        "isp": primary.get("isp", ""),
        "egress_ips": ips,
        "inside_china": _is_cn(country_zh),
        "source": primary["source"],
        "warn": warn,
        "failures": failures,
    }


def to_qweather_location(geo: dict) -> str:
    """和风天气支持「经度,纬度」（最多两位小数）。"""
    return f"{float(geo['lon']):.2f},{float(geo['lat']):.2f}"

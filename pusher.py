"""消息推送：企业微信群机器人 webhook + MagicPush 统一 API。"""
import logging

import requests

log = logging.getLogger("weather-notify")

# 企业微信机器人内容上限（字节）：markdown 4096、text 2048，各留余量
WECOM_LIMITS = {"markdown": 4000, "text": 2000}


def push_wecom(webhook: str, content: str, msg_type: str = "markdown",
               timeout: int = 10) -> None:
    """推送到企业微信群机器人。

    msg_type:
        markdown  群内渲染样式（## / 引用 / 颜色），企微客户端看效果最好
        text      纯文本，微信侧接收企业微信消息时不渲染 markdown，只能用这个
    """
    mt = "text" if str(msg_type).lower() == "text" else "markdown"
    limit = WECOM_LIMITS[mt]
    raw = content.encode("utf-8")
    if len(raw) > limit:
        marker = "\n…(截断)"
        keep = limit - len(marker.encode("utf-8"))
        content = raw[:keep].decode("utf-8", "ignore") + marker

    if mt == "text":
        payload = {"msgtype": "text", "text": {"content": content}}
    else:
        payload = {"msgtype": "markdown", "markdown": {"content": content}}

    resp = requests.post(webhook, json=payload, timeout=timeout)
    try:
        result = resp.json()
    except ValueError:
        raise RuntimeError(f"企业微信返回非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}")
    if result.get("errcode") != 0:
        raise RuntimeError(
            f"企业微信推送失败: errcode={result.get('errcode')} "
            f"errmsg={result.get('errmsg') or '无'}（HTTP {resp.status_code}）")
    log.info("企业微信推送成功（%s）", mt)


def push_magicpush(base_url: str, token: str, title: str, content: str,
                   msg_type: str = "markdown", timeout: int = 10) -> None:
    """推送到 MagicPush 统一推送接口。

    POST {base_url}/api/push
    Authorization: Bearer {token}
    {"title": ..., "content": ..., "type": "text" | "markdown" | "html"}
    """
    url = base_url.rstrip("/") + "/api/push"
    payload = {"title": title, "content": content, "type": msg_type}
    resp = requests.post(url, json=payload,
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=timeout)
    try:
        result = resp.json()
    except ValueError:
        if resp.status_code >= 400:
            raise RuntimeError(f"MagicPush 推送失败: HTTP {resp.status_code} {resp.text[:200]}")
        log.info("MagicPush 推送完成（HTTP %s）", resp.status_code)
        return
    # 不同版本返回结构略有差异，仅在明确失败时报错
    if result.get("success") is False or result.get("code") not in (None, 0, 200, "0", "200", 1000):
        raise RuntimeError(f"MagicPush 推送失败: {result}")
    log.info("MagicPush 推送成功")

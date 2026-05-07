"""
微信推送模块 — 通过 PushPlus 推送访客登记通知到保安微信

PushPlus: 关注公众号 → 注册 → 拿 Token → HTTP POST
免费额度 200 次/天，无需企业认证。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from config.livekit_config import PUSHPLUS_TOKEN

logger = logging.getLogger("park-visitor.wechat_push")

PUSHPLUS_API = "http://www.pushplus.plus/send"


def format_wechat_message(record: dict) -> tuple[str, str]:
    """将访客记录格式化为 PushPlus 消息（markdown）

    Returns:
        (title, content) — 标题和 markdown 正文
    """
    title = "访客登记通知"
    lines = ["## 访客登记通知", ""]
    if record.get("license_plate"):
        lines.append(f"- **车牌**：{record['license_plate']}")
    if record.get("visiting_company"):
        lines.append(f"- **来访单位**：{record['visiting_company']}")
    if record.get("visitor_name"):
        lines.append(f"- **访客姓名**：{record['visitor_name']}")
    if record.get("visitor_phone"):
        lines.append(f"- **联系电话**：{record['visitor_phone']}")
    elif record.get("caller_number"):
        lines.append(f"- **联系电话**：{record['caller_number']}")
    if record.get("purpose"):
        lines.append(f"- **来访事由**：{record['purpose']}")
    lines.append(f"- **时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return title, "\n".join(lines)


async def push_visitor_to_security(record: dict) -> bool:
    """推送访客登记信息给保安微信（通过 PushPlus）

    Args:
        record: 访客记录字典

    Returns:
        True 表示推送成功
    """
    if not PUSHPLUS_TOKEN:
        logger.warning("PUSHPLUS_TOKEN 未配置，跳过微信推送")
        return False

    title, content = format_wechat_message(record)
    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "markdown",
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(PUSHPLUS_API, json=payload)
                result = resp.json()
                if result.get("code") == 200:
                    logger.info(f"PushPlus 推送成功: {title}")
                    return True
                else:
                    logger.error(f"PushPlus 推送失败: {result}")
        except Exception as e:
            logger.error(f"PushPlus 推送异常 (attempt {attempt + 1}): {e}")

        if attempt == 0:
            await asyncio.sleep(1)

    return False

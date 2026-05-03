"""
微信推送模块 — 访客登记通知推送

当前为占位实现，日志记录替代实际推送。
未来集成个人微信 API 后替换函数体即可。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("park-visitor.wechat_push")


def format_wechat_message(record: dict) -> str:
    """将访客记录格式化为微信消息文本"""
    lines = ["【访客登记通知】"]
    if record.get("license_plate"):
        lines.append(f"车牌：{record['license_plate']}")
    if record.get("visiting_company"):
        lines.append(f"来访单位：{record['visiting_company']}")
    if record.get("visitor_name"):
        lines.append(f"访客姓名：{record['visitor_name']}")
    if record.get("visitor_phone"):
        lines.append(f"联系电话：{record['visitor_phone']}")
    elif record.get("caller_number"):
        lines.append(f"联系电话：{record['caller_number']}")
    if record.get("purpose"):
        lines.append(f"来访事由：{record['purpose']}")
    lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


async def push_visitor_to_security(record: dict) -> bool:
    """推送访客登记信息给门卫微信

    TODO: 未来集成个人微信 API（如 wechaty / openclaw）
    当前为占位实现，仅记录日志

    Args:
        record: 访客记录字典

    Returns:
        True 表示推送成功（占位）
    """
    message = format_wechat_message(record)
    logger.info(f"WeChat push placeholder: would send visitor info to security guard\n{message}")
    return True

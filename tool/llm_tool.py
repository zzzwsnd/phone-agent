"""
LLM 工具集 — LangGraph 节点中使用的业务工具

这些工具被 LLM 在对话中调用，执行预约查询、确认、转接等操作
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger("outbound-caller.tools")


@tool
async def look_up_availability(date: str) -> dict:
    """查询指定日期的可用预约时段。

    Args:
        date: 要查询的日期，如 "2025-06-15"
    """
    logger.info(f"查询可用时段: {date}")
    # TODO: 替换为真实的预约系统 API 调用
    await asyncio.sleep(1)  # 模拟网络延迟
    return {
        "available_times": ["上午 9:00", "上午 10:30", "下午 2:00", "下午 3:30"],
    }


@tool
async def confirm_appointment(date: str, time: str) -> str:
    """确认预约。仅在用户明确确认日期和时间后调用。

    Args:
        date: 预约日期
        time: 预约时间
    """
    logger.info(f"确认预约: {date} {time}")
    # TODO: 替换为真实的预约系统写入
    return f"预约已确认：{date} {time}"


@tool
async def end_call() -> str:
    """用户希望结束通话时调用。"""
    logger.info("用户请求结束通话")
    return "call_ended"


@tool
async def transfer_call() -> str:
    """将通话转接给人工坐席。需在用户确认转接意图后调用。"""
    logger.info("转接至人工坐席")
    return "call_transferred"


@tool
async def detected_answering_machine() -> str:
    """检测到语音信箱时调用。听到语音信箱问候语后使用。"""
    logger.info("检测到语音信箱，挂断")
    return "voicemail_detected"


# ── 工具列表（供 LangGraph 绑定到 LLM） ─────────────────────────────────────
ALL_TOOLS = [
    look_up_availability,
    confirm_appointment,
    end_call,
    transfer_call,
    detected_answering_machine,
]

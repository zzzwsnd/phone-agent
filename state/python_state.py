"""
通话状态定义 — LangGraph 图的状态结构

每次通话对应一个图实例，状态在整个对话生命周期中流转
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class CallState(TypedDict, total=False):
    """外呼通话的完整状态"""

    # ── 输入字段（调度时传入） ──────────────────────────────────────────────
    phone_number: str                    # 被叫号码
    transfer_to: str                     # 转接目标号码
    customer_name: str                   # 客户姓名
    appointment_time: str                # 预约时间

    # ── 通话控制字段 ──────────────────────────────────────────────────────
    call_status: str                     # dialing / ringing / connected / voicemail / ended / transferred
    next_action: str                     # 图节点路由: greet / chat / lookup / confirm / transfer / end / done

    # ── 对话历史摘要（控制 context 大小） ──────────────────────────────────
    conversation_summary: str            # 对话摘要，由 summarizer 节点维护
    turn_count: int                      # 当前轮次计数
    messages: Annotated[list[dict], operator.add]  # 累积的消息列表，支持并行写入

    # ── 业务数据 ──────────────────────────────────────────────────────────
    available_times: Optional[list[str]] # 查询到的可用时段
    confirmed_date: Optional[str]        # 确认的预约日期
    confirmed_time: Optional[str]        # 确认的预约时间

    # ── 错误处理 ──────────────────────────────────────────────────────────
    error: Optional[str]                 # 错误信息

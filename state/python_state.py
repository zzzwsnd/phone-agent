"""
通话状态定义 — 访客呼入登记的状态结构

每次通话对应一个状态实例，在对话生命周期中流转
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class CallState(TypedDict, total=False):
    """访客呼入登记的完整状态"""

    # ── 呼入标识 ──────────────────────────────────────────────────────────────
    caller_number: str                    # 呼入主叫号码（从 SIP participant attributes 提取）

    # ── 访客登记字段 ──────────────────────────────────────────────────────────
    license_plate: Optional[str]          # 车牌号，如"沪A12345"
    visiting_company: Optional[str]       # 来访单位
    # visitor_phone 由 caller_number 自动填充，不再单独采集
    purpose: Optional[str]                # 来访事由（送货、开会、面试等）
    visitor_name: Optional[str]           # 访客姓名

    # ── 回访识别 ──────────────────────────────────────────────────────────────
    is_return_visit: bool                 # 是否回访（主叫号码命中历史记录）
    return_visit_summary: Optional[str]   # 预注入的回访摘要

    # ── 业务数据 ──────────────────────────────────────────────────────────────
    visitor_record_id: Optional[int]      # 保存后的 DB 记录 ID
    call_room_name: Optional[str]         # LiveKit room 名（DB 记录追溯）

    # ── 通话控制字段 ──────────────────────────────────────────────────────────
    call_status: str                      # inbound_ringing / connected / saving / ended / transferred

    # ── 对话历史摘要 ──────────────────────────────────────────────────────────
    conversation_summary: str             # 对话摘要
    turn_count: int                       # 当前轮次计数
    messages: Annotated[list[dict], operator.add]  # 累积的消息列表，支持并行写入

    # ── 错误处理 ──────────────────────────────────────────────────────────────
    error: Optional[str]                  # 错误信息

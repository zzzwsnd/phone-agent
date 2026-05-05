"""
语音工具集 — LiveKit 通话控制操作

工厂模式：create_voice_tools(state) 返回绑定到当前 CallState 的 function_tool 列表。
Agent 构造时通过 tools= 参数传入。
"""
from __future__ import annotations

import logging
from typing import Optional

from livekit import api
from livekit.agents import (
    function_tool,
    RunContext,
    get_job_context,
)

from state.python_state import CallState

logger = logging.getLogger("park-visitor.voice_tools")


async def _hangup():
    """挂断通话：通过删除房间来结束呼叫。"""
    job_ctx = get_job_context()
    await job_ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=job_ctx.room.name)
    )


def create_voice_tools(state: CallState):
    """创建绑定到当前通话状态的语音工具函数。

    Args:
        state: 通话状态实例，存储访客采集字段和上下文信息
    """

    @function_tool()
    async def update_visitor_info(
        ctx: RunContext,
        license_plate: Optional[str] = None,
        visiting_company: Optional[str] = None,
        purpose: Optional[str] = None,
        visitor_name: Optional[str] = None,
    ):
        """从访客话语中提取到一个或多个字段时调用，记录到当前采集状态。所有参数均为字符串类型，不知道的参数不要传。

        Args:
            license_plate: 车牌号，字符串类型（可选）
            visiting_company: 来访单位，字符串类型（可选）
            purpose: 来访事由，字符串类型（可选）
            visitor_name: 访客姓名，字符串类型（可选）
        """
        # 合并新提取的字段（None 和空值不覆盖已有值）
        fields = {
            "license_plate": license_plate,
            "visiting_company": visiting_company,
            "purpose": purpose,
            "visitor_name": visitor_name,
        }
        for key, val in fields.items():
            if val and str(val).strip():
                state[key] = str(val).strip()

        logger.info(f"更新采集状态: {dict((k, v) for k, v in state.items() if v and k in ('license_plate', 'visiting_company', 'purpose', 'visitor_name'))}")

        # 构建返回摘要
        collected_items = [f"{k}={state[k]}" for k in ('license_plate', 'visiting_company', 'purpose', 'visitor_name') if state.get(k)]
        missing = []
        if not state.get("purpose"):
            missing.append("来访事由")
        if not state.get("visiting_company") and not state.get("visitor_name"):
            missing.append("来访单位或访客姓名")

        result_parts = [f"已采集: {', '.join(collected_items)}"]
        if missing:
            result_parts.append(f"待采集: {', '.join(missing)}")
            result_parts.append("请立即追问待采集字段，不要调用 save_visitor_record")
        else:
            result_parts.append("全部必填已齐，请调用 save_visitor_record 保存")

        return "；".join(result_parts)

    @function_tool()
    async def save_visitor_record(
        ctx: RunContext,
        reason: Optional[str] = None,
    ):
        """访客信息采集完毕后调用，保存访客记录、推送通知并结束通话。不需要传任何参数。

        Args:
            reason: 保存原因备注，字符串类型（可选，不需要传）
        """
        from infra.visitor_db import save_visitor_record as db_save
        from infra.wechat_push import push_visitor_to_security

        record_data = {
            "caller_number": state.get("caller_number", ""),
            "license_plate": state.get("license_plate", ""),
            "visiting_company": state.get("visiting_company", ""),
            "visitor_phone": state.get("caller_number", ""),  # 直接用主叫号码
            "purpose": state.get("purpose", ""),
            "visitor_name": state.get("visitor_name", ""),
        }

        logger.info(f"保存访客记录: {record_data}")

        try:
            record_id = db_save(
                caller_number=record_data["caller_number"],
                license_plate=record_data["license_plate"] or None,
                visiting_company=record_data["visiting_company"] or None,
                visitor_phone=record_data["visitor_phone"] or None,
                purpose=record_data["purpose"] or None,
                visitor_name=record_data["visitor_name"] or None,
                call_room_name=state.get("call_room_name", ""),
            )
            logger.info(f"访客记录已保存, id={record_id}")

            # 微信推送（失败不影响保存结果）
            try:
                await push_visitor_to_security(record_data)
            except Exception as push_err:
                logger.error(f"微信推送失败（记录已保存）: {push_err}")

            # 礼貌告别后挂断
            await ctx.session.generate_reply(
                instructions="告知访客记录已保存、已通知门卫放行，礼貌告别"
            )
            current_speech = ctx.session.current_speech
            if current_speech:
                await current_speech.wait_for_playout()
            await _hangup()

            return "访客记录已保存，通话已结束"
        except Exception as e:
            logger.error(f"保存访客记录失败: {e}")
            return f"保存失败: {e}"

    @function_tool()
    async def end_call(
        ctx: RunContext,
        reason: Optional[str] = None,
    ):
        """结束通话。不需要传任何参数。适用于：访客辱骂/恶意骚扰、信息不齐超时、或其他需要结束通话的场景。

        Args:
            reason: 结束原因备注，字符串类型（可选，不需要传）
        """
        logger.info("结束通话")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await _hangup()

    return [update_visitor_info, save_visitor_record, end_call]

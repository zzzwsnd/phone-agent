"""
语音工具集 — LiveKit 通话控制操作

这些是 LiveKit Agent 的 function_tool，由 AI 在对话中直接调用
控制挂断、转接、访客记录保存等操作
"""
from __future__ import annotations

import logging
from typing import Any

from livekit import rtc, api
from livekit.agents import (
    function_tool,
    RunContext,
    get_job_context,
)

logger = logging.getLogger("park-visitor.voice_tools")


async def hangup():
    """挂断通话：通过删除房间来结束呼叫。"""
    job_ctx = get_job_context()
    await job_ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=job_ctx.room.name)
    )


def create_voice_tools(visitor_context: dict[str, Any], participant: rtc.RemoteParticipant | None):
    """创建绑定到当前通话上下文的语音工具函数。

    Args:
        visitor_context: 访客上下文，含 caller_number, transfer_to, call_room_name
        participant: 来电方的 RemoteParticipant
    """

    @function_tool()
    async def save_visitor_record(
        ctx: RunContext,
        caller_number: str,
        license_plate: str,
        visiting_company: str,
        visitor_phone: str,
        purpose: str,
        visitor_name: str,
    ):
        """访客信息采集完毕后调用，保存访客登记记录。

        Args:
            caller_number: 呼入主叫号码
            license_plate: 车牌号
            visiting_company: 来访单位
            visitor_phone: 访客联系电话
            purpose: 来访事由
            visitor_name: 访客姓名
        """
        from infra.visitor_db import save_visitor_record as db_save
        from infra.wechat_push import push_visitor_to_security

        logger.info(f"保存访客记录: caller={caller_number}, plate={license_plate}, company={visiting_company}")

        try:
            record_id = db_save(
                caller_number=caller_number,
                license_plate=license_plate or None,
                visiting_company=visiting_company or None,
                visitor_phone=visitor_phone or None,
                purpose=purpose or None,
                visitor_name=visitor_name or None,
                call_room_name=visitor_context.get("call_room_name", ""),
            )
            logger.info(f"访客记录已保存, id={record_id}")

            # 推送微信通知（占位）
            record = {
                "caller_number": caller_number,
                "license_plate": license_plate,
                "visiting_company": visiting_company,
                "visitor_phone": visitor_phone,
                "purpose": purpose,
                "visitor_name": visitor_name,
            }
            await push_visitor_to_security(record)

            return f"访客记录已保存（ID: {record_id}），已通知门卫"
        except Exception as e:
            logger.error(f"保存访客记录失败: {e}")
            return f"保存失败: {e}"

    @function_tool()
    async def transfer_call(ctx: RunContext):
        """访客要求找人工/保安时调用，转接通话。"""
        transfer_to = visitor_context.get("transfer_to")
        if not transfer_to:
            return "无法转接：未配置保安转接号码"

        logger.info(f"转接通话至保安: {transfer_to}")

        await ctx.session.generate_reply(
            instructions="告知访客即将转接给保安，请稍候"
        )

        job_ctx = get_job_context()
        try:
            await job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=job_ctx.room.name,
                    participant_identity=participant.identity if participant else "",
                    transfer_to=f"tel:{transfer_to}",
                )
            )
            logger.info(f"转接成功: {transfer_to}")
        except Exception as e:
            logger.error(f"转接失败: {e}")
            await ctx.session.generate_reply(
                instructions="转接出现问题，请稍后再试"
            )
            await hangup()

    @function_tool()
    async def end_call(ctx: RunContext):
        """访客信息已保存或通话结束时调用。"""
        logger.info("结束通话")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await hangup()

    return [save_visitor_record, transfer_call, end_call]

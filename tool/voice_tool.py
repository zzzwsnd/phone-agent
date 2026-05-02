"""
语音工具集 — LiveKit 通话控制操作

这些是 LiveKit Agent 的 function_tool，由 AI 在对话中直接调用
控制 SIP 拨号、挂断、转接等通话操作
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from livekit import rtc, api
from livekit.agents import (
    function_tool,
    RunContext,
    get_job_context,
)

logger = logging.getLogger("outbound-caller.voice_tools")


async def hangup():
    """挂断通话：通过删除房间来结束呼叫。"""
    job_ctx = get_job_context()
    await job_ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=job_ctx.room.name)
    )


def create_voice_tools(dial_info: dict[str, Any], participant: rtc.RemoteParticipant | None):
    """创建绑定到当前通话上下文的语音工具函数。

    因为 LiveKit 的 function_tool 需要绑定到 Agent 实例，
    这里返回一个工具列表，供 OutboundCaller 使用。
    """

    @function_tool()
    async def transfer_call(ctx: RunContext):
        """将通话转接给人工坐席，需在用户确认后调用。"""
        transfer_to = dial_info.get("transfer_to")
        if not transfer_to:
            return "无法转接：未配置转接号码"

        logger.info(f"转接通话至 {transfer_to}")

        await ctx.session.generate_reply(
            instructions="告知用户即将转接给人工坐席，请稍候"
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
        """用户希望结束通话时调用。"""
        logger.info(f"结束通话")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await hangup()

    @function_tool()
    async def look_up_availability(ctx: RunContext, date: str):
        """用户询问其他预约时间时调用，查询指定日期的可用时段。

        Args:
            date: 要查询可用时间的日期
        """
        logger.info(f"查询可用时段: {date}")
        await asyncio.sleep(2)
        return {"available_times": ["上午 9:00", "上午 10:30", "下午 2:00", "下午 3:30"]}

    @function_tool()
    async def confirm_appointment(ctx: RunContext, date: str, time: str):
        """用户确认预约时调用，仅在用户确定日期和时间后使用。

        Args:
            date: 预约日期
            time: 预约时间
        """
        logger.info(f"确认预约: {date} {time}")
        return "预约已确认"

    @function_tool()
    async def detected_answering_machine(ctx: RunContext):
        """检测到语音信箱后调用，在听到语音信箱问候语后使用。"""
        logger.info("检测到语音信箱，挂断")
        await hangup()

    return [transfer_call, end_call, look_up_availability, confirm_appointment, detected_answering_machine]

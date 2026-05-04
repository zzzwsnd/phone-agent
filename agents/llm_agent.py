"""
工业园区访客呼入 Agent — 纯 LiveKit AgentSession 驱动

架构：
  LiveKit AgentSession 管理 STT → LLM → TTS 语音管道
  Agent 的 function_tool 处理业务操作（保存访客记录、转接、挂断）
  系统 prompt 约束 LLM 行为（3 轮采集、何时保存、何时结束）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中，支持从任意目录启动
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from livekit import rtc, api
from livekit.agents import (
    AgentSession,
    Agent,
    JobContext,
    function_tool,
    RunContext,
    get_job_context,
    cli,
    WorkerOptions,
    RoomInputOptions,
)
from livekit.plugins import deepgram, cartesia, silero, noise_cancellation
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import LLM as OpenAILLM

from prompts.llm_prompy import SYSTEM_PROMPT

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("park-visitor-agent")


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def extract_caller_number(participant: rtc.RemoteParticipant) -> str:
    """从 SIP participant 提取主叫号码

    优先读取 SIP attributes，回退解析 participant identity。
    LiveKit SIP identity 格式常为 "sip_<number>"。
    """
    # 优先从 attributes 获取
    caller_number = participant.attributes.get("sip.caller_number", "")
    if caller_number:
        return caller_number

    # 回退：从 identity 解析
    identity = participant.identity
    if identity.startswith("sip_"):
        return identity[4:]

    return identity


# ══════════════════════════════════════════════════════════════════════════════
# LiveKit Agent — 访客呼入登记
# ══════════════════════════════════════════════════════════════════════════════

class InboundAgent(Agent):
    """工业园区访客呼入登记 Agent

    纯 LiveKit AgentSession 驱动，无 LangGraph。
    系统提示词约束 LLM 在 3 轮内采集访客信息并保存。
    """

    def __init__(
        self,
        *,
        caller_number: str,
        return_visit_summary: str = "",
        transfer_to: str = "",
    ):
        # 构建回访信息段
        if return_visit_summary:
            return_visit_section = f"## 回访信息\n{return_visit_summary}"
        else:
            return_visit_section = ""

        # 生成系统提示词
        instructions = SYSTEM_PROMPT.format(
            caller_number=caller_number,
            return_visit_section=return_visit_section,
        )

        super().__init__(instructions=instructions)

        # 通话上下文
        self.caller_number = caller_number
        self.return_visit_summary = return_visit_summary
        self.visitor_context: dict[str, Any] = {
            "caller_number": caller_number,
            "transfer_to": transfer_to,
            "call_room_name": "",
        }
        self.participant: rtc.RemoteParticipant | None = None

    def set_participant(self, participant: rtc.RemoteParticipant, room_name: str = ""):
        """设置远端参与者和房间名"""
        self.participant = participant
        self.visitor_context["call_room_name"] = room_name

    # ── LiveKit function_tools ─────────────────────────────────────────────

    @function_tool()
    async def save_visitor_record(
        self,
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

        logger.info(
            f"保存访客记录: caller={caller_number}, plate={license_plate}, "
            f"company={visiting_company}, purpose={purpose}"
        )

        try:
            record_id = db_save(
                caller_number=caller_number,
                license_plate=license_plate or None,
                visiting_company=visiting_company or None,
                visitor_phone=visitor_phone or None,
                purpose=purpose or None,
                visitor_name=visitor_name or None,
                call_room_name=self.visitor_context.get("call_room_name", ""),
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
    async def transfer_call(self, ctx: RunContext):
        """访客要求找人工/保安时调用，转接通话。"""
        transfer_to = self.visitor_context.get("transfer_to")
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
                    participant_identity=self.participant.identity if self.participant else "",
                    transfer_to=f"tel:{transfer_to}",
                )
            )
            logger.info(f"转接成功: {transfer_to}")
        except Exception as e:
            logger.error(f"转接失败: {e}")
            await ctx.session.generate_reply(
                instructions="转接出现问题，请稍后再试"
            )
            await self.hangup()

    @function_tool()
    async def end_call(self, ctx: RunContext):
        """访客信息已保存或通话结束时调用。"""
        logger.info("结束通话")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await self.hangup()

    async def hangup(self):
        """挂断通话：通过删除房间来结束呼叫。"""
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


# ══════════════════════════════════════════════════════════════════════════════
# 入口函数 — 呼入模式
# ══════════════════════════════════════════════════════════════════════════════

async def inbound_entrypoint(ctx: JobContext):
    """呼入入口函数，由 LiveKit Agents 框架在收到调度任务时调用。

    呼入流程：SIP trunk 自动创建 Room → Agent dispatch 加入 → 等待来电方
    """
    logger.info(f"连接房间: {ctx.room.name}")
    await ctx.connect()

    # 等待来电方加入（SIP inbound 自动创建 participant）
    participant = await ctx.wait_for_participant()
    logger.info(f"来电方加入: {participant.identity}")

    # 提取主叫号码
    caller_number = extract_caller_number(participant)
    logger.info(f"主叫号码: {caller_number}")

    # 查询回访信息（预注入）
    from infra.visitor_db import lookup_visitor_by_phone, format_return_visit_summary
    try:
        previous_records = lookup_visitor_by_phone(caller_number)
        is_return_visit = len(previous_records) > 0
        return_visit_summary = format_return_visit_summary(previous_records)
        if is_return_visit:
            logger.info(f"回访识别: {return_visit_summary}")
    except Exception as e:
        logger.warning(f"回访查询失败（继续作为新访客处理）: {e}")
        is_return_visit = False
        return_visit_summary = ""

    # 获取保安转接号码
    import os
    transfer_to = os.getenv("SECURITY_TRANSFER_NUMBER", "")

    # 创建 Agent 实例
    agent = InboundAgent(
        caller_number=caller_number,
        return_visit_summary=return_visit_summary,
        transfer_to=transfer_to,
    )
    agent.set_participant(participant, room_name=ctx.room.name)

    # 配置语音管道：STT → LLM → TTS（中文场景）
    session = AgentSession(
        turn_detection=MultilingualModel(),
        vad=silero.VAD.load(),
        stt=deepgram.STT(language="zh-CN", model="nova-3"),
        tts=cartesia.TTS(language="zh", model="sonic-3"),
        llm=OpenAILLM(model="gpt-4o", temperature=0.7),
        min_endpointing_delay=1.5,
    )

    # ── 管道事件监听：可视化 STT/LLM/TTS 每一步 ──────────────────────────────
    @session.on("user_input_transcribed")
    def _on_stt(ev):
        logger.info(f"[STT] 识别结果: {ev.transcript} (final={ev.is_final})")

    @session.on("response_started")
    def _on_llm_start(ev):
        logger.info("[LLM] 开始生成回复...")

    @session.on("response_done")
    def _on_llm_done(ev):
        text = ev.output.transcript if hasattr(ev, 'output') and hasattr(ev.output, 'transcript') else str(ev)
        logger.info(f"[LLM] 回复完成: {text}")

    @session.on("speech_created")
    def _on_tts(ev):
        logger.info(f"[TTS] 开始语音合成...")

    # 启动会话（无 SIP 拨号 — 来电方已在房间中）
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLI 启动
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=inbound_entrypoint,
            agent_name="park-visitor-agent",
        )
    )

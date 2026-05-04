"""
工业园区访客呼入 Agent — 纯 LiveKit AgentSession 驱动

架构：
  LiveKit AgentSession 管理 STT → LLM → TTS 语音管道
  Agent 的 function_tool 处理业务操作（保存访客记录并自动挂断）
  系统 prompt 约束 LLM 行为（3 轮采集、主动开场、何时保存）
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
from livekit.plugins import volcengine, silero, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import LLM as OpenAILLM

from config.livekit_config import (
    VOLCENGINE_STT_APP_ID, VOLCENGINE_STT_CLUSTER, VOLCENGINE_STT_ACCESS_TOKEN,
    VOLCENGINE_TTS_APP_ID, VOLCENGINE_TTS_CLUSTER, VOLCENGINE_TTS_ACCESS_TOKEN,
    VOLCENGINE_LLM_API_KEY, VOLCENGINE_LLM_MODEL, VOLCENGINE_LLM_BASE_URL,
)
from prompts.llm_prompy import SYSTEM_PROMPT, GREET_INSTRUCTION

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
    两个 function_tool：update_visitor_info（实时提取字段）+ confirm_and_save（校验落库挂断）。
    """

    def __init__(
        self,
        *,
        caller_number: str,
        return_visit_summary: str = "",
        greet_instruction: str = "",
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

        # 追加开场白指令
        if greet_instruction:
            instructions += f"\n\n## 开场白指令\n{greet_instruction}"

        super().__init__(instructions=instructions)

        # 通话上下文
        self.caller_number = caller_number
        self.return_visit_summary = return_visit_summary
        self.visitor_context: dict[str, Any] = {
            "caller_number": caller_number,
            "call_room_name": "",
        }
        self.participant: rtc.RemoteParticipant | None = None

        # 实时采集状态
        self.collected: dict[str, str] = {}

    def set_participant(self, participant: rtc.RemoteParticipant, room_name: str = ""):
        """设置远端参与者和房间名"""
        self.participant = participant
        self.visitor_context["call_room_name"] = room_name

    # ── LiveKit function_tools ─────────────────────────────────────────────

    @function_tool()
    async def update_visitor_info(
        self,
        ctx: RunContext,
        license_plate: str = "",
        visiting_company: str = "",
        visitor_phone: str = "",
        purpose: str = "",
        visitor_name: str = "",
    ):
        """从访客话语中提取到一个或多个字段时调用，记录到当前采集状态。

        Args:
            license_plate: 车牌号（可选）
            visiting_company: 来访单位（可选）
            visitor_phone: 访客联系电话（可选）
            purpose: 来访事由（可选）
            visitor_name: 访客姓名（可选）
        """
        # 合并新提取的字段（空值不覆盖已有值）
        fields = {
            "license_plate": license_plate,
            "visiting_company": visiting_company,
            "visitor_phone": visitor_phone,
            "purpose": purpose,
            "visitor_name": visitor_name,
        }
        for key, val in fields.items():
            if val:
                self.collected[key] = val

        logger.info(f"更新采集状态: {self.collected}")

        # 构建返回摘要
        collected_items = [f"{k}={v}" for k, v in self.collected.items() if v]
        missing = []
        if not self.collected.get("purpose"):
            missing.append("来访事由")
        if not self.collected.get("visiting_company") and not self.collected.get("visitor_name"):
            missing.append("来访单位或访客姓名")

        result_parts = [f"已记录: {', '.join(collected_items)}"]
        if missing:
            result_parts.append(f"待采集: {', '.join(missing)}")
            result_parts.append("请追问缺失字段")
        else:
            result_parts.append("必填字段已齐，可以调用 confirm_and_save 保存")

        return "；".join(result_parts)

    @function_tool()
    async def confirm_and_save(self, ctx: RunContext):
        """必填字段采集完毕后调用，保存访客记录、推送通知并结束通话。

        内部校验：必填字段不齐则拒绝保存并返回缺失字段。
        """
        # 校验必填字段
        missing = []
        if not self.collected.get("purpose"):
            missing.append("来访事由")
        if not self.collected.get("visiting_company") and not self.collected.get("visitor_name"):
            missing.append("来访单位或访客姓名")

        if missing:
            logger.warning(f"必填字段缺失，拒绝保存: {missing}")
            return f"必填字段未齐，缺少: {', '.join(missing)}，请继续采集"

        from infra.visitor_db import save_visitor_record as db_save
        from infra.wechat_push import push_visitor_to_security

        record_data = {
            "caller_number": self.caller_number,
            "license_plate": self.collected.get("license_plate", ""),
            "visiting_company": self.collected.get("visiting_company", ""),
            "visitor_phone": self.collected.get("visitor_phone", ""),
            "purpose": self.collected.get("purpose", ""),
            "visitor_name": self.collected.get("visitor_name", ""),
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
                call_room_name=self.visitor_context.get("call_room_name", ""),
            )
            logger.info(f"访客记录已保存, id={record_id}")

            await push_visitor_to_security(record_data)

            # 礼貌告别后挂断
            await ctx.session.generate_reply(
                instructions="告知访客记录已保存、已通知门卫放行，礼貌告别"
            )
            current_speech = ctx.session.current_speech
            if current_speech:
                await current_speech.wait_for_playout()
            await self._hangup()

            return "访客记录已保存，通话已结束"
        except Exception as e:
            logger.error(f"保存访客记录失败: {e}")
            return f"保存失败: {e}"

    async def _hangup(self):
        """挂断通话：通过删除房间来结束呼叫。"""
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


# ══════════════════════════════════════════════════════════════════════════════
# 语音管道构建 — 火山引擎 STT → LLM → TTS
# ══════════════════════════════════════════════════════════════════════════════

def build_agent_session() -> AgentSession:
    """构建火山引擎语音管道：STT → LLM → TTS

    所有配置从 .env.local 经 config/livekit_config.py 读取。
    """
    return AgentSession(
        turn_detection=MultilingualModel(),
        vad=silero.VAD.load(),
        stt=volcengine.STT(
            app_id=VOLCENGINE_STT_APP_ID,
            cluster=VOLCENGINE_STT_CLUSTER,
        ),
        tts=volcengine.TTS(
            app_id=VOLCENGINE_TTS_APP_ID,
            cluster=VOLCENGINE_TTS_CLUSTER,
            voice="BV001_V2_streaming",
        ),
        llm=OpenAILLM(
            model=VOLCENGINE_LLM_MODEL,
            base_url=VOLCENGINE_LLM_BASE_URL,
            api_key=VOLCENGINE_LLM_API_KEY,
            temperature=0.7,
        ),
        min_endpointing_delay=1.5,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 入口函数 — 呼入模式
# ══════════════════════════════════════════════════════════════════════════════

async def inbound_entrypoint(ctx: JobContext):
    """呼入入口函数，由 LiveKit Agents 框架在收到调度任务时调用。

    呼入流程：SIP trunk 自动创建 Room → Agent dispatch 加入 → 等待来电方
    """
    # 校验火山引擎必填配置
    missing = []
    if not VOLCENGINE_STT_APP_ID:
        missing.append("VOLCENGINE_STT_APP_ID")
    if not VOLCENGINE_TTS_APP_ID:
        missing.append("VOLCENGINE_TTS_APP_ID")
    if not VOLCENGINE_STT_ACCESS_TOKEN:
        missing.append("VOLCENGINE_STT_ACCESS_TOKEN")
    if not VOLCENGINE_TTS_ACCESS_TOKEN:
        missing.append("VOLCENGINE_TTS_ACCESS_TOKEN")
    if not VOLCENGINE_LLM_API_KEY:
        missing.append("VOLCENGINE_LLM_API_KEY")
    if missing:
        logger.error(f"缺少火山引擎必填配置: {', '.join(missing)}，请在 .env.local 中设置")
        return

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
    return_visit_summary = ""
    try:
        previous_records = lookup_visitor_by_phone(caller_number)
        is_return_visit = len(previous_records) > 0
        return_visit_summary = format_return_visit_summary(previous_records) if is_return_visit else ""
        if is_return_visit:
            logger.info(f"回访识别: {return_visit_summary}")
    except Exception as e:
        logger.warning(f"回访查询失败（继续作为新访客处理）: {e}")
        is_return_visit = False

    # 根据是否回访选择开场白
    greet_instruction = GREET_INSTRUCTION

    # 创建 Agent 实例
    agent = InboundAgent(
        caller_number=caller_number,
        return_visit_summary=return_visit_summary,
        greet_instruction=greet_instruction,
    )
    agent.set_participant(participant, room_name=ctx.room.name)

    # 配置语音管道：STT → LLM → TTS（火山引擎）
    session = build_agent_session()

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

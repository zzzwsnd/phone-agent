"""
工业园区访客呼入 Agent — 纯 LiveKit AgentSession 驱动

架构：
  LiveKit AgentSession 管理 STT → LLM → TTS 语音管道
  工厂模式 function_tool（voice_tool.py）处理业务操作
  系统 prompt 约束 LLM 行为（3 轮采集、主动开场、何时保存）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
# 确保项目根目录在 sys.path 中，支持从任意目录启动
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from livekit import rtc, api
from livekit.agents import (
    AgentSession,
    Agent,
    JobContext,
    cli,
    WorkerOptions,
)
from livekit.agents.voice.room_io import RoomOptions, AudioInputOptions
from livekit.plugins import volcengine, silero, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import LLM as OpenAILLM

from config.livekit_config import (
    VOLCENGINE_STT_APP_ID, VOLCENGINE_STT_CLUSTER, VOLCENGINE_STT_ACCESS_TOKEN,
    VOLCENGINE_TTS_APP_ID, VOLCENGINE_TTS_CLUSTER, VOLCENGINE_TTS_ACCESS_TOKEN,
    VOLCENGINE_LLM_API_KEY, VOLCENGINE_LLM_MODEL, VOLCENGINE_LLM_BASE_URL,
)
from prompts.llm_prompy import SYSTEM_PROMPT, GREET_INSTRUCTION
from state.python_state import CallState
from tool.voice_tool import create_voice_tools

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
    Tools 由 voice_tool.py 工厂创建，通过 tools= 参数传入。
    """

    def __init__(
        self,
        *,
        caller_number: str,
        return_visit_summary: str = "",
        greet_instruction: str = "",
        tools: list | None = None,
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

        super().__init__(instructions=instructions, tools=tools or [])


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
            access_token=VOLCENGINE_STT_ACCESS_TOKEN,
        ),
        tts=volcengine.TTS(
            app_id=VOLCENGINE_TTS_APP_ID,
            cluster=VOLCENGINE_TTS_CLUSTER,
            access_token=VOLCENGINE_TTS_ACCESS_TOKEN,
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

    # 创建通话状态
    state: CallState = {
        "caller_number": caller_number,
        "call_room_name": ctx.room.name,
        "is_return_visit": is_return_visit,
        "return_visit_summary": return_visit_summary,
        "call_status": "connected",
    }
    # 查询回访信息（预注入）
    from infra.visitor_db import lookup_visitor_by_phone, format_return_visit_summary
    return_visit_summary = ""
    try:
        previous_records = lookup_visitor_by_phone(caller_number)
        is_return_visit = len(previous_records) > 0
        return_visit_summary = format_return_visit_summary(previous_records) if is_return_visit else ""
        if is_return_visit:
            state["is_return_visit"] = is_return_visit
            state["return_visit_summary"] = return_visit_summary
            # 预填最近一次记录的字段，便于回访确认后直接保存
            latest = previous_records[0]
            if latest.get("license_plate"):
                state["license_plate"] = latest["license_plate"]
            if latest.get("visiting_company"):
                state["visiting_company"] = latest["visiting_company"]
            if latest.get("purpose"):
                state["purpose"] = latest["purpose"]
            if latest.get("visitor_name"):
                state["visitor_name"] = latest["visitor_name"]
            logger.info(f"回访识别: {return_visit_summary}")
    except Exception as e:
        logger.warning(f"回访查询失败（继续作为新访客处理）: {e}")
        is_return_visit = False

    # 根据是否回访选择开场白
    greet_instruction = GREET_INSTRUCTION



    # 创建工具（绑定到 CallState）
    tools = create_voice_tools(state)

    # 创建 Agent 实例
    agent = InboundAgent(
        caller_number=caller_number,
        return_visit_summary=return_visit_summary,
        greet_instruction=greet_instruction,
        tools=tools,
    )

    # 配置语音管道：STT → LLM → TTS（火山引擎）
    session = build_agent_session()

    # ── 管道事件监听 ──────────────────────────────────────────────────────────
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
        logger.info("[TTS] 开始语音合成...")

    # 启动会话（无 SIP 拨号 — 来电方已在房间中）
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=RoomOptions(
            audio_input=AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        ),
    )

    # LLM 根据 prompt 自行开场，不硬编码问候语

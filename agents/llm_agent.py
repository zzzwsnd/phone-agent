"""
工业园区访客呼入 Agent — 纯 LiveKit AgentSession 驱动

架构：
  LiveKit AgentSession 管理 STT → LLM → TTS 语音管道
  系统 prompt 约束 LLM 行为（3 轮采集、主动开场、何时保存）
"""
from __future__ import annotations
from config.agent_config import build_agent_session
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
# 确保项目根目录在 sys.path 中，支持从任意目录启动
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# ── Monkey-patch: 修复 GLM-5.1 tool call 参数中未加引号的字符串值 ──────────────
# GLM-5.1 有时输出 {"license_plate": A12345} 而非 {"license_plate": "A12345"}，
# 导致 pydantic_core.from_json 严格解析失败。此 patch 在解析失败时尝试正则修复。
import livekit.agents.llm.utils as _llm_utils
_original_prepare = _llm_utils.prepare_function_arguments

_UNQUOTED_VALUE_RE = re.compile(
    r'(?<=[,\{\[:\s])\s*([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*(?=[,\}\]\s:])'
)

def _fix_unquoted_json(raw: str) -> str:
    """将 JSON 中未加引号的字符串值用双引号包裹。"""
    # 反复修复直到没有变化或解析成功
    for _ in range(3):
        fixed = _UNQUOTED_VALUE_RE.sub(r' "\1"', raw)
        if fixed == raw:
            break
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            raw = fixed
            continue
    return fixed

def _patched_prepare_function_arguments(*, fnc, json_arguments, call_ctx=None):
    if isinstance(json_arguments, str):
        # 1) 截断/空 JSON 兜底：只有 { 或空字符串时用 {} 代替
        stripped = json_arguments.strip()
        if not stripped or stripped == "{" or stripped == "}":
            logger.warning(f"修复截断 tool call JSON: {json_arguments!r} → {{}}")
            return _original_prepare(fnc=fnc, json_arguments="{}", call_ctx=call_ctx)

        try:
            return _original_prepare(fnc=fnc, json_arguments=json_arguments, call_ctx=call_ctx)
        except (ValueError, Exception):
            # 2) 尝试修复未加引号的字符串值
            fixed = _fix_unquoted_json(json_arguments)
            if fixed != json_arguments:
                logger.warning(f"修复 tool call JSON: {json_arguments!r} → {fixed!r}")
                try:
                    return _original_prepare(fnc=fnc, json_arguments=fixed, call_ctx=call_ctx)
                except Exception:
                    pass
            # 3) 仍失败，尝试补全截断的 JSON 再解析
            for attempt in [json_arguments + "}", json_arguments.rstrip(",") + "}"]:
                try:
                    return _original_prepare(fnc=fnc, json_arguments=attempt, call_ctx=call_ctx)
                except Exception:
                    continue
            logger.error(f"无法修复 tool call JSON: {json_arguments!r}")
            raise
    return _original_prepare(fnc=fnc, json_arguments=json_arguments, call_ctx=call_ctx)

_llm_utils.prepare_function_arguments = _patched_prepare_function_arguments
# ── Monkey-patch 结束 ──────────────────────────────────────────────────────────

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

    # 强制添加开场白，避免 LLM 硬编码开场白
    if is_return_visit:
        # 回访：根据回访摘要确认访客
        company = state.get("visiting_company", "")
        purpose = state.get("purpose", "")
        name = state.get("visitor_name", "")
        plate = state.get("license_plate", "")

        parts = []
        if name:
            parts.append(f"{name}您好")
        else:
            parts.append("您好")
        if company or purpose:
            detail = f"来{company}" if company else ""
            detail += f"送{purpose}" if purpose and not detail else (purpose if purpose and not detail else "")
            if detail:
                parts.append(f"今天是不是和上次一样{detail}？")
            else:
                parts.append("今天还是和上次一样吗？")
        else:
            parts.append("今天还是和上次一样吗？")

        greet_text = "，".join(parts) if len(parts) > 1 else parts[0]
        if not greet_text.endswith("？"):
            greet_text += "？"
    else:
        # 新访客：简洁提问
        greet_text = "您好，请问车牌号多少，今天找哪家公司，什么事儿？"

    #await session.generate_reply(instructions=greet_text)
    await session.say(greet_text)
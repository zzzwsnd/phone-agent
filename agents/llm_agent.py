"""
外呼智能体 — LangGraph 状态图 + LiveKit 语音框架

架构：
  LangGraph 负责对话逻辑流转（greet → chat → 业务工具 → end）
  LiveKit Agent 负责语音管道（STT → LLM → TTS）和 SIP 通话控制
  两者通过 OutboundCaller Agent 桥接
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

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

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from state.python_state import CallState
from prompts.llm_prompy import SYSTEM_PROMPT, GREET_INSTRUCTION, CHAT_INSTRUCTION
from config.livekit_config import SIP_OUTBOUND_TRUNK_ID
from config.agent_config import fast_llm
from tool.voice_tool import create_voice_tools, hangup

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("outbound-caller")


# ══════════════════════════════════════════════════════════════════════════════
# LangGraph 状态图 — 定义对话逻辑流转
# ══════════════════════════════════════════════════════════════════════════════

def greet_node(state: CallState) -> dict:
    """开场白节点：生成问候语，确认预约信息"""
    prompt = GREET_INSTRUCTION.format(
        appointment_time=state.get("appointment_time", "待确认")
    )
    response = fast_llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT.format(
            customer_name=state.get("customer_name", "患者"),
            appointment_time=state.get("appointment_time", "待确认"),
        )),
        HumanMessage(content=prompt),
    ])
    return {
        "messages": [{"role": "assistant", "content": response.content}],
        "next_action": "chat",
        "turn_count": 1,
    }


def chat_node(state: CallState) -> dict:
    """对话节点：处理用户输入，决定下一步动作"""
    # 构建消息历史
    messages = [SystemMessage(content=SYSTEM_PROMPT.format(
        customer_name=state.get("customer_name", "患者"),
        appointment_time=state.get("appointment_time", "待确认"),
    ))]

    # 添加对话历史（最近 10 轮，控制 context 大小）
    history = state.get("messages", [])[-20:]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    # 添加决策指令
    messages.append(HumanMessage(content=CHAT_INSTRUCTION))

    response = fast_llm.invoke(messages)
    content = response.content

    # 根据回复内容判断下一步路由
    next_action = "chat"  # 默认继续对话
    if "确认" in content and "预约" in content:
        next_action = "confirm"
    elif "查询" in content or "时段" in content or "可用" in content:
        next_action = "lookup"
    elif "转接" in content or "人工" in content:
        next_action = "transfer"
    elif "结束" in content or "再见" in content or "挂断" in content:
        next_action = "end"
    elif "语音信箱" in content:
        next_action = "voicemail"

    return {
        "messages": [{"role": "assistant", "content": content}],
        "next_action": next_action,
        "turn_count": state.get("turn_count", 0) + 1,
    }


def route_after_chat(state: CallState) -> str:
    """条件边：根据 next_action 决定下一个节点"""
    action = state.get("next_action", "chat")
    # 超过 50 轮自动结束，防止死循环
    if state.get("turn_count", 0) > 50:
        return "end"
    # 映射动作到节点名
    routing = {
        "chat": "chat",
        "confirm": "chat",      # 确认由 LLM 工具处理，回到 chat
        "lookup": "chat",       # 查询由 LLM 工具处理，回到 chat
        "transfer": "end",      # 转接 → 结束
        "end": "end",
        "voicemail": "end",     # 语音信箱 → 结束
        "done": END,
    }
    return routing.get(action, "chat")


def build_call_graph() -> StateGraph:
    """构建通话状态图"""
    graph = StateGraph(CallState)

    # 添加节点
    graph.add_node("greet", greet_node)
    graph.add_node("chat", chat_node)

    # 设置入口
    graph.set_entry_point("greet")

    # 添加边
    graph.add_edge("greet", "chat")
    graph.add_conditional_edges("chat", route_after_chat, {
        "chat": "chat",
        "end": END,
    })

    return graph.compile()


# ══════════════════════════════════════════════════════════════════════════════
# LiveKit Agent — 语音管道 + SIP 通话控制
# ══════════════════════════════════════════════════════════════════════════════

class OutboundCaller(Agent):
    """外呼代理：牙科诊所预约确认助理

    继承 LiveKit Agent，负责语音对话管道。
    对话逻辑由 LangGraph 状态图驱动（见上方 build_call_graph），
    但实际语音交互通过 LiveKit AgentSession 的 STT→LLM→TTS 管道完成。
    """

    def __init__(
        self,
        *,
        name: str,
        appointment_time: str,
        dial_info: dict[str, Any],
    ):
        # LangGraph 状态图实例（用于外部状态查询和测试）
        self.call_graph = build_call_graph()

        # 系统提示词，由模板生成
        instructions = SYSTEM_PROMPT.format(
            customer_name=name,
            appointment_time=appointment_time,
        )

        super().__init__(instructions=instructions)

        # 通话上下文
        self.participant: rtc.RemoteParticipant | None = None
        self.dial_info = dial_info

    def set_participant(self, participant: rtc.RemoteParticipant):
        """设置远端参与者引用"""
        self.participant = participant

    # ── LiveKit function_tools（AI 在对话中直接调用） ──────────────────────

    @function_tool()
    async def transfer_call(self, ctx: RunContext):
        """将通话转接给人工坐席，需在用户确认后调用。"""
        transfer_to = self.dial_info.get("transfer_to")
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
                    participant_identity=self.participant.identity,
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
        """用户希望结束通话时调用。"""
        logger.info(f"结束通话")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await self.hangup()

    @function_tool()
    async def look_up_availability(self, ctx: RunContext, date: str):
        """用户询问其他预约时间时调用，查询指定日期的可用时段。

        Args:
            date: 要查询可用时间的日期
        """
        logger.info(f"查询可用时段: {date}")
        await asyncio.sleep(2)  # 模拟网络延迟
        return {"available_times": ["上午 9:00", "上午 10:30", "下午 2:00", "下午 3:30"]}

    @function_tool()
    async def confirm_appointment(self, ctx: RunContext, date: str, time: str):
        """用户确认预约时调用，仅在用户确定日期和时间后使用。

        Args:
            date: 预约日期
            time: 预约时间
        """
        logger.info(f"确认预约: {date} {time}")
        return "预约已确认"

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """检测到语音信箱后调用，在听到语音信箱问候语后使用。"""
        logger.info("检测到语音信箱，挂断")
        await self.hangup()

    async def hangup(self):
        """挂断通话"""
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


# ══════════════════════════════════════════════════════════════════════════════
# 入口函数 — LiveKit Worker 调度时调用
# ══════════════════════════════════════════════════════════════════════════════

async def entrypoint(ctx: JobContext):
    """代理入口函数，由 LiveKit Agents 框架在收到调度任务时调用。"""
    logger.info(f"连接房间: {ctx.room.name}")
    await ctx.connect()

    # 解析调度元数据
    dial_info = json.loads(ctx.job.metadata)
    participant_identity = phone_number = dial_info["phone_number"]

    # 创建代理实例
    agent = OutboundCaller(
        name=dial_info.get("customer_name", "患者"),
        appointment_time=dial_info.get("appointment_time", "待确认"),
        dial_info=dial_info,
    )

    # 配置语音管道：STT → LLM → TTS
    session = AgentSession(
        turn_detection=EnglishModel(),
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        tts=cartesia.TTS(),
        llm=ChatOpenAI(model="gpt-4o", temperature=0.7),  # 用 LangChain LLM
    )

    # 先启动会话再拨号，确保不遗漏任何语音输入
    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # 通过 SIP 拨打电话
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )

        await session_started
        participant = await ctx.wait_for_participant(identity=participant_identity)
        logger.info(f"参与者加入: {participant.identity}")
        agent.set_participant(participant)

    except api.TwirpError as e:
        logger.error(
            f"SIP 呼叫失败: {e.message}, "
            f"SIP 状态: {e.metadata.get('sip_status_code')} "
            f"{e.metadata.get('sip_status')}"
        )
        ctx.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
# CLI 启动
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller",
        )
    )

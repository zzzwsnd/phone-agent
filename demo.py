from __future__ import annotations  # 允许在类型标注中使用 str | int 这种新语法，即使 Python 版本较旧也能用

import asyncio                       # 异步编程库，用来写 async/await 异步代码
import logging                       # 日志库，用来打印运行信息到控制台
from dotenv import load_dotenv       # 从 .env 文件读取环境变量的库
import json                          # JSON 解析库，用来把字符串转成字典
import os                            # 操作系统接口库，用来读取环境变量等
from typing import Any               # 类型标注工具，Any 表示"任意类型"

from livekit import rtc, api         # rtc=实时通信模块，api=调用 LiveKit 服务的接口
from livekit.agents import (         # LiveKit Agents 框架核心组件
    AgentSession,                     # 代理会话，管理 STT/LLM/TTS 整个对话管道
    Agent,                            # 代理基类，我们的 OutboundCaller 继承自它
    JobContext,                       # 任务上下文，包含房间信息、API 调用能力等
    function_tool,                    # 装饰器，把方法注册为 AI 可调用的工具
    RunContext,                       # 工具函数运行时的上下文，可访问当前会话
    get_job_context,                  # 获取当前任务上下文的函数
    cli,                              # 命令行接口，用来启动 worker
    WorkerOptions,                    # Worker 配置项
    RoomInputOptions,                 # 房间音频输入配置
)
from langchain_llm import LangChainLLM  # LangChain LLM 适配器，将 LangChain 接入 LiveKit
from livekit.plugins import (        # LiveKit 插件，提供各种 AI 模型的接入
    deepgram,                         # Deepgram 语音识别（STT）
    cartesia,                         # Cartesia 语音合成（TTS）
    silero,                           # Silero 语音活动检测（VAD），判断人是否在说话
    noise_cancellation,               # Krisp 背景噪音消除
)
from livekit.plugins.turn_detector.english import EnglishModel
# 英语轮次检测模型，判断对方说完了没有，决定何时接话


# 读取 .env.local 文件，把里面的变量加载到环境变量中，仅用于本地开发
load_dotenv(dotenv_path="../.env.local")
logger = logging.getLogger("outbound-caller")  # 创建一个名为 "outbound-caller" 的日志记录器
logger.setLevel(logging.INFO)                   # 设置日志级别为 INFO，只打印 INFO 及以上级别的日志

# 从环境变量读取 SIP 外呼中继 ID，决定电话从哪条线路拨出
outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")


class OutboundCaller(Agent):          # 定义 OutboundCaller 类，继承自 Agent 基类
    """外呼代理，扮演牙科诊所的预约确认助理。"""

    def __init__(                      # 构造函数，创建实例时自动调用
            self,
            *,                             # * 后面的参数必须用关键字传递，不能按位置传
            name: str,                     # 客户姓名，类型为字符串
            appointment_time: str,         # 预约时间，类型为字符串
            dial_info: dict[str, Any],     # 拨号信息字典，值为任意类型
    ):
        super().__init__(              # 调用父类 Agent 的构造函数
            instructions=f"""          # f-string 格式化的系统提示词，定义 AI 的角色和行为规则
            You are a scheduling assistant for a dental practice. Your interface with user will be voice.
            You will be on a call with a patient who has an upcoming appointment. Your goal is to confirm the appointment details.
            As a customer service representative, you will be polite and professional at all times. Allow user to end the conversation.

            When the user would like to be transferred to a human agent, first confirm with them. upon confirmation, use the transfer_call tool.
            The customer's name is {name}. His appointment is on {appointment_time}.
            """
        )
        # 远端参与者引用，初始为 None，后续在用户接听后设置，用于通话转接等功能
        self.participant: rtc.RemoteParticipant | None = None

        # 保存拨号信息字典，包含 phone_number（被叫号码）和 transfer_to（转接目标号码）
        self.dial_info = dial_info

    def set_participant(self, participant: rtc.RemoteParticipant):
        """设置远端参与者引用，在用户接听加入房间后调用。"""
        self.participant = participant  # 把用户接听后加入房间的参与者对象保存下来

    async def hangup(self):            # async 表示这是异步方法，需要用 await 调用
        """挂断通话：通过删除房间来结束呼叫。"""

        job_ctx = get_job_context()    # 获取当前任务的上下文对象
        await job_ctx.api.room.delete_room(  # 调用 LiveKit API 删除房间（等同于挂断）
            api.DeleteRoomRequest(      # 构造删除房间请求
                room=job_ctx.room.name, # 指定要删除的房间名
            )
        )

    @function_tool()                   # 装饰器：将此方法注册为 AI 可调用的工具
    async def transfer_call(self, ctx: RunContext, Exception=None):
        """将通话转接给人工坐席，需在用户确认后调用。"""

        transfer_to = self.dial_info["transfer_to"]  # 取出转接目标号码
        if not transfer_to:            # 如果没有配置转接号码
            return "cannot transfer call"  # 返回错误信息，AI 会据此告知用户

        logger.info(f"transferring call to {transfer_to}")  # 打印转接日志

        # 先让 AI 生成一段语音告知用户即将转接，说完后再执行转接操作
        await ctx.session.generate_reply(
            instructions="let the user know you'll be transferring them"
        )

        job_ctx = get_job_context()    # 获取任务上下文
        try:                           # 尝试执行转接
            # 调用 LiveKit SIP API 将当前参与者转接到目标电话号码
            await job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(  # 构造转接请求
                    room_name=job_ctx.room.name,    # 当前房间名
                    participant_identity=self.participant.identity,  # 被转接的参与者
                    transfer_to=f"tel:{transfer_to}",  # 转接目标，tel: 格式表示电话号码
                )
            )

            logger.info(f"transferred call to {transfer_to}")  # 转接成功日志
        except Exception as e:         # 如果转接出错
            logger.error(f"error transferring call: {e}")  # 打印错误日志
            # 让 AI 告知用户转接出错
            await ctx.session.generate_reply(
                instructions="there was an error transferring the call."
            )
            # 转接失败，直接挂断
            await self.hangup()

    @function_tool()                   # 装饰器：将此方法注册为 AI 可调用的工具
    async def end_call(self, ctx: RunContext):
        """用户希望结束通话时调用。"""
        logger.info(f"ending the call for {self.participant.identity}")

        # 获取 AI 当前正在播放的语音，等待它说完再挂断，避免话语被截断
        current_speech = ctx.session.current_speech
        if current_speech:             # 如果 AI 正在说话
            await current_speech.wait_for_playout()  # 等待它播完

        await self.hangup()            # 然后挂断

    @function_tool()                   # 装饰器：将此方法注册为 AI 可调用的工具
    async def look_up_availability(
            self,
            ctx: RunContext,
            date: str,                     # AI 会自动从用户对话中提取 date 参数
    ):
        """用户询问其他预约时间时调用，查询指定日期的可用时段。

        Args:
            date: 要查询可用时间的日期
        """
        logger.info(
            f"looking up availability for {self.participant.identity} on {date}"
        )
        # 模拟网络请求延迟 3 秒，实际项目中应替换为真实的预约系统 API 调用
        await asyncio.sleep(3)
        # 返回模拟的可用时段数据
        return {
            "available_times": ["1pm", "2pm", "3pm"],
        }

    @function_tool()                   # 装饰器：将此方法注册为 AI 可调用的工具
    async def confirm_appointment(
            self,
            ctx: RunContext,
            date: str,                     # AI 从对话中提取的预约日期
            time: str,                     # AI 从对话中提取的预约时间
    ):
        """用户确认预约时调用，仅在用户确定日期和时间后使用。

        Args:
            date: 预约日期
            time: 预约时间
        """
        logger.info(
            f"confirming appointment for {self.participant.identity} on {date} at {time}"
        )
        return "reservation confirmed"  # 返回确认结果

    @function_tool()                   # 装饰器：将此方法注册为 AI 可调用的工具
    async def detected_answering_machine(self, ctx: RunContext):
        """检测到语音信箱后调用，在听到语音信箱问候语后使用。"""
        logger.info(f"detected answering machine for {self.participant.identity}")
        await self.hangup()            # 检测到是语音信箱就直接挂断，不浪费时间


async def entrypoint(ctx: JobContext):  # 框架在收到调度任务时调用此函数
    """代理入口函数，由 LiveKit Agents 框架在收到调度任务时调用。"""
    logger.info(f"connecting to room {ctx.room.name}")  # 打印正在连接的房间名
    await ctx.connect()                # 连接到 LiveKit 房间

    # 解析调度时传入的 JSON 元数据字符串，转成字典，包含拨号信息：
    # - phone_number: 被叫电话号码
    # - transfer_to: 转接目标号码
    dial_info = json.loads(ctx.job.metadata)
    # 被叫号码同时作为参与者的身份标识
    participant_identity = phone_number = dial_info["phone_number"]

    # 创建代理实例，传入客户姓名和预约详情（此处为硬编码示例值）
    agent = OutboundCaller(
        name="Jayden",
        appointment_time="next Tuesday at 3pm",
        dial_info=dial_info,           # 传入拨号信息
    )

    # 配置 AI 管道，各模型组件协作完成语音对话：
    # 英语轮次检测 → 语音活动检测 → 语音转文字 → 大语言模型 → 文字转语音
    session = AgentSession(
        turn_detection=EnglishModel(),  # 英语轮次检测，判断用户是否说完
        vad=silero.VAD.load(),          # 语音活动检测，判断有没有人在说话
        stt=deepgram.STT(),             # 语音转文字（用户说话→文字）
        tts=cartesia.TTS(),             # 文字转语音（AI 回复→语音），可选：使用 openai.TTS()
        llm=LangChainLLM(model="gpt-4o"),  # 使用 LangChain 封装的大语言模型，生成 AI 回复
    )

    # 先异步启动会话再拨号，确保用户接听时代理不会遗漏任何语音输入
    session_started = asyncio.create_task(  # create_task 不等待完成，立即继续往下执行
        session.start(                  # 启动会话
            agent=agent,                # 绑定代理
            room=ctx.room,              # 绑定房间
            room_input_options=RoomInputOptions(
                # 启用 Krisp 通话级噪音消除，适用于嘈杂的通话环境
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # 通过 SIP 创建参与者，开始拨打电话
    try:
        await ctx.api.sip.create_sip_participant(  # 调用 SIP API 拨打电话
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,            # 通话所在的房间
                sip_trunk_id=outbound_trunk_id,      # 使用哪条 SIP 线路拨出
                sip_call_to=phone_number,            # 拨打的目标号码
                participant_identity=participant_identity,  # 参与者身份标识
                wait_until_answered=True,            # 等待对方接听后才继续执行
            )
        )

        # 等待会话启动完成
        await session_started
        # 等待 SIP 参与者（接听方）加入房间
        participant = await ctx.wait_for_participant(identity=participant_identity)
        logger.info(f"participant joined: {participant.identity}")

        # 保存参与者引用，供转接等工具使用
        agent.set_participant(participant)

    except api.TwirpError as e:        # SIP 调用出错（Twirp 是 LiveKit 的 RPC 协议）
        logger.error(
            f"error creating SIP participant: {e.message}, "
            f"SIP status: {e.metadata.get('sip_status_code')} "  # SIP 状态码
            f"{e.metadata.get('sip_status')}"                     # SIP 状态描述
        )
        # SIP 呼叫失败，关闭当前任务
        ctx.shutdown()


if __name__ == "__main__":             # 当直接运行此文件时（而非被其他文件 import）
    cli.run_app(                       # 启动 LiveKit Agent Worker
        WorkerOptions(                 # Worker 配置
            entrypoint_fnc=entrypoint,  # 指定入口函数
            agent_name="outbound-caller",  # Worker 名称，调度时用这个名字匹配
        )
    )

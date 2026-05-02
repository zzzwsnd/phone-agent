# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概述

一个基于 LiveKit Agents 的外呼代理，通过 SIP 拨打电话号码，并使用 AI 管道（STT → LLM → TTS）进行语音对话。角色设定为牙科诊所的预约助理，可以确认预约、查询可用时间、检测语音信箱，以及将通话转接给人工坐席。

## 开发命令

```shell
# 环境搭建（Windows）
py -3 -m venv venv
source venv/Scripts/activate   # 或：powershell venv/Scripts/Activate.ps1
pip install -r requirements.txt
python llm_agent.py download-files

# 本地运行代理
python llm_agent.py dev

# 发起外呼（需另开终端，需要 `lk` CLI）
lk dispatch create --new-room --agent-name outbound-caller --metadata '{"phone_number": "+1234567890", "transfer_to": "+9876543210"}'
```

本项目未配置测试套件、代码检查工具或 CI 流水线。

## 架构

整个应用位于 `agent.py` 中。核心组件：

- **OutboundCaller(Agent)**：LiveKit Agent 子类。构造时接收 `name`（姓名）、`appointment_time`（预约时间）和 `dial_info`（包含 `phone_number` 和 `transfer_to`）。系统指令由这些字段模板化生成。
- **AgentSession**：配置了管道式模型——Deepgram STT、GPT-4o LLM、Cartesia TTS、Silero VAD 和英语轮次检测。替代方案：切换为 `openai.realtime.RealtimeModel()` 使用端到端语音模型。
- **Function tools**：`end_call`、`transfer_call`、`look_up_availability`、`confirm_appointment`、`detected_answering_machine`。这些是代理在对话中可调用的动作。
- **entrypoint(ctx)**：连接 LiveKit 房间，启动代理会话，然后调用 `create_sip_participant` 拨打用户电话。SIP 参与者身份标识即为电话号码。

### 通话流程

1. 代理 Worker 接收到调度请求，元数据 JSON 中包含 `phone_number` 和 `transfer_to`
2. `entrypoint` 连接房间，启动 `AgentSession`，并通过 SIP 拨号
3. 用户接听后，代理开始对话
4. 工具处理语音信箱检测、预约查询/确认、转接和挂断
5. `hangup()` 通过删除房间来结束通话

## 环境变量

在 `.env.local` 中配置（已被 gitignore）。必填：`LIVEKIT_URL`、`LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET`、`OPENAI_API_KEY`、`SIP_OUTBOUND_TRUNK_ID`。选填：`DEEPGRAM_API_KEY`、`CARTESIA_API_KEY`。

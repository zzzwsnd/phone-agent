# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LiveKit Agents 的工业园区访客呼入登记 AI 系统。访客拨打电话 → SIP 呼入 → AI 接听 → 自然对话采集信息（车牌、来访单位、手机号、事由、姓名）→ 保存记录 → 推送门卫微信 → 保安确认放行。约束 25 秒内完成，3 轮对话约 15 秒。

## 开发命令

```shell
# 环境搭建（Windows）
py -3 -m venv venv
source venv/Scripts/activate   # 或：powershell venv/Scripts/Activate.ps1
pip install -r requirements.txt

# 启动 API 服务（端口 8090）
python main.py

# 启动 Agent Worker（需另开终端）
python agents/llm_agent.py dev
```

本项目无测试套件、lint 配置或 CI 流水线。

## 架构

项目采用纯 LiveKit AgentSession 架构：AgentSession 驱动 STT → LLM → TTS 语音管道，系统 prompt 约束 LLM 行为，`@function_tool` 处理业务操作。无 LangGraph/LangChain。

### 核心文件

- **`agents/llm_agent.py`** — 主入口，包含 InboundAgent 类、inbound_entrypoint 函数、extract_caller_number 工具函数
- **`api/pthon_api.py`** — FastAPI 路由，提供 `/health`、`/visitors`、`/visitors/{phone}` 接口（`/call` 已标记废弃）
- **`main.py`** — API 服务启动入口（uvicorn, port 8090），启动时初始化 DB 表
- **`state/python_state.py`** — `CallState` TypedDict，定义访客登记状态字段
- **`prompts/llm_prompy.py`** — 系统提示词和开场白指令
- **`tool/voice_tool.py`** — LiveKit function_tool 工厂（供 Agent 语音对话使用）
- **`config/agent_config.py`** — LLM 模型配置（支持 DeepSeek/Doubao/Qwen/OpenAI，通过 `MODEL_PROVIDER` 环境变量切换）
- **`config/livekit_config.py`** — LiveKit 和 SIP 连接配置
- **`infra/mysql.py`** — MySQL 连接池
- **`infra/schema.py`** — visitor_records 表 DDL 和建表函数
- **`infra/visitor_db.py`** — 访客记录数据库操作（保存、查询、回访摘要）
- **`infra/wechat_push.py`** — 微信推送占位模块

### 工具系统

项目只有一层 LiveKit `@function_tool`，定义在 `agents/llm_agent.py` 的 `InboundAgent` 类上：

1. **`save_visitor_record`** — 保存访客记录到 MySQL + 推送微信通知
2. **`transfer_call`** — 转接人工保安
3. **`end_call`** — 结束通话

### 对话流

```
SIP 呼入 → Room 自动创建 → Agent dispatch 加入 → 提取主叫号码 → 查 DB 回访
→ 创建 InboundAgent（prompt 含回访摘要）→ AgentSession 启动
→ LLM 自主对话（prompt 约束 3 轮内完成）→ function_tool 保存/转接/挂断
```

无状态图路由，LLM 根据 system prompt 自主决定何时调用哪个 tool。

### 通话流程

1. 访客拨打呼入号码，SIP trunk 自动创建 LiveKit Room
2. Agent Worker 收到 dispatch，连接 Room，等待来电方加入
3. 从 SIP participant attributes 提取主叫号码，查 DB 判断回访
4. 创建 InboundAgent（prompt 注入回访摘要），启动 AgentSession
5. LLM 在 3 轮内采集访客信息，调用 save_visitor_record 保存
6. 保存后推送微信通知给门卫，结束通话

### LLM 配置

AgentSession 使用 `OpenAI(model="gpt-4o", temperature=0.7)`。`config/agent_config.py` 导出的 LLM 实例（`supervisor_llm`/`fast_llm`/`reasoning_llm`）为历史遗留，当前未被使用。

## 环境变量

在 `.env.local` 中配置（已被 gitignore）。

**必填**：
- `LIVEKIT_URL` — LiveKit 服务地址
- `LIVEKIT_API_KEY` — LiveKit API Key
- `LIVEKIT_API_SECRET` — LiveKit API Secret
- `SIP_INBOUND_TRUNK_ID` — SIP 呼入 Trunk ID

**选填**：
- `SIP_OUTBOUND_TRUNK_ID` — SIP 外呼 Trunk ID（外呼模式，已废弃）
- `SECURITY_TRANSFER_NUMBER` — 保安转接号码
- `DEEPGRAM_API_KEY` — STT 服务 Key
- `CARTESIA_API_KEY` — TTS 服务 Key
- `INBOUND_AGENT_NAME` — 呼入 Agent 名称（默认 park-visitor-agent）

**MySQL**：
- `MYSQL_HOST`（默认 localhost）
- `MYSQL_PORT`（默认 3306）
- `MYSQL_DATABASE`（默认 mibo）
- `MYSQL_USER`（默认 root）
- `MYSQL_PASSWORD`

**模型**：
- `MODEL_PROVIDER`（默认 deepseek）、对应提供商的 API Key

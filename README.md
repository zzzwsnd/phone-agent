# 工业园区访客呼入登记 AI 系统

基于 LiveKit Agents 的语音 AI 门卫。访客拨打电话 → AI 接听 → 3 轮对话采集信息 → 保存记录 → 微信通知保安放行。

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          外部服务                                    │
│                                                                      │
│  ┌──────────┐   ┌──────────────┐   ┌──────────┐   ┌──────────────┐ │
│  │  SIP Trunk│   │ 火山引擎 STT │   │火山方舟LLM│   │ 火山引擎 TTS │ │
│  │  (电话网关)│   │  (语音识别)   │   │ (GLM-5.1) │   │  (语音合成)   │ │
│  └─────┬─────┘   └──────┬───────┘   └─────┬────┘   └──────┬───────┘ │
│        │                │                  │               │         │
└────────┼────────────────┼──────────────────┼───────────────┼─────────┘
         │                │                  │               │
         ▼                ▼                  ▼               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      LiveKit Cloud                                   │
│                                                                      │
│  ┌─────────────┐    ┌───────────────────────────────────────────┐   │
│  │ SIP → Room  │───▶│           Room (音频流)                    │   │
│  │  自动创建    │    │  ┌─────────┐        ┌──────────────┐     │   │
│  └─────────────┘    │  │ 访客     │◀──音频──▶│ Agent        │     │   │
│                     │  │Participant│        │ Participant  │     │   │
│                     │  └─────────┘        └──────┬───────┘     │   │
│                     └───────────────────────────┼──────────────┘   │
└─────────────────────────────────────────────────┼──────────────────┘
                                                   │
                                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Agent Worker (Python)                             │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    AgentSession (语音管道)                     │   │
│  │                                                               │   │
│  │   访客音频 ──▶ STT ──▶ LLM ──▶ TTS ──▶ 播放给访客            │   │
│  │                  │      │      │                              │   │
│  │                  │      ▼      │                              │   │
│  │                  │  ┌─────────┐│                              │   │
│  │                  │  │function ││                              │   │
│  │                  │  │  tools  ││                              │   │
│  │                  │  └────┬────┘│                              │   │
│  └──────────────────────────┼─────┘                              │   │
│                              │                                    │   │
│                    ┌─────────┼─────────┐                          │   │
│                    ▼         ▼         ▼                          │   │
│              ┌─────────┐ ┌───────┐ ┌────────┐                    │   │
│              │MySQL    │ │PushPlus│ │挂断通话 │                    │   │
│              │保存记录  │ │微信推送 │ │        │                    │   │
│              └─────────┘ └───────┘ └────────┘                    │   │
│                                                                    │   │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────┐  │   │
│  │API 服务    │  │ 通话状态     │  │ 提示词模板  │  │ 配置中心  │  │   │
│  │(FastAPI)   │  │ (CallState)  │  │            │  │          │  │   │
│  └────────────┘  └──────────────┘  └────────────┘  └──────────┘  │   │
└─────────────────────────────────────────────────────────────────────┘
```

## 通话流程

```
访客拨号
  │
  ▼
SIP Trunk 呼入 ──▶ LiveKit 自动创建 Room
  │
  ▼
Agent Worker 收到 dispatch，连接 Room
  │
  ▼
等待来电方加入 ──▶ 从 SIP attributes 提取主叫号码
  │
  ▼
查 MySQL 判断是否回访 ──▶ 回访：预填历史字段，开场确认
  │                      新访客：直接提问
  ▼
创建 InboundAgent（prompt 注入回访摘要）
  │
  ▼
AgentSession 启动语音管道（STT → LLM → TTS）
  │
  ▼
┌──────── LLM 自主对话（3 轮内完成）──────────┐
│                                              │
│  访客说话 → STT 识别 → LLM 理解             │
│  LLM 判断：                                  │
│    ├─ 提取到字段 → update_visitor_info        │
│    ├─ 全部齐了  → save_visitor_record         │
│    └─ 辱骂骚扰  → end_call                   │
│                                              │
└──────────────────────────────────────────────┘
  │
  ▼
save_visitor_record
  ├─ 保存到 MySQL
  ├─ PushPlus 推送微信通知保安
  └─ 礼貌告别 → 挂断
```

## 核心文件

```
outbound-caller-python/
├── agents/
│   └── llm_agent.py        # 主入口：InboundAgent 类、呼入入口、Monkey-patch
├── api/
│   └── pthon_api.py        # FastAPI 路由：/health, /visitors, /visitors/{phone}
├── config/
│   ├── livekit_config.py   # 环境变量配置（LiveKit、火山引擎、PushPlus）
│   └── agent_config.py     # LLM 模型配置（历史遗留，未使用）
├── infra/
│   ├── mysql.py            # MySQL 连接池
│   ├── schema.py           # visitor_records 表 DDL
│   ├── visitor_db.py       # 访客记录 CRUD + 回访查询
│   └── wechat_push.py      # PushPlus 微信推送
├── prompts/
│   └── llm_prompy.py       # 系统提示词 + 开场白指令
├── state/
│   └── python_state.py     # CallState TypedDict 状态定义
├── tool/
│   └── voice_tool.py       # function_tool 工厂（update/save/end）
├── main.py                 # API 服务启动入口（uvicorn :8090）
├── cli_main.py             # CLI 启动入口
└── .env.local              # 环境变量（gitignore）
```

## 设计决策

### 1. 纯 AgentSession 架构，无状态机

```
传统方案：                          本项目方案：
┌───────┐    ┌───────┐             ┌───────────────┐
│ 采集  │───▶│ 确认  │───▶ 保存    │               │
│ 状态  │    │ 状态  │             │  LLM 自主决策  │
└───────┘    └───────┘             │  (prompt 约束) │
                                    │               │
需要定义状态转移图、                 │  3轮内完成采集  │
边、条件分支                        │  齐了就保存    │
                                    └───────────────┘
```

**原因**：访客对话天然不规律（打断、补充、跳序），状态机难以穷举所有路径。用 system prompt 约束 LLM 行为 + function_tool 暴露操作，LLM 自主判断何时采集、何时保存，开发成本极低且容错性强。

### 2. 语音管道选火山引擎

```
访客音频 ──▶ 火山引擎 STT ──▶ GLM-5.1 ──▶ 火山引擎 TTS ──▶ 播放
              (中文识别强)     (中文理解强)   (中文合成自然)
```

**原因**：面向国内工业园区访客，全链路中文优化。火山引擎 STT/TTS 对中文方言、工业环境噪声有较好鲁棒性；GLM-5.1 通过火山方舟 ARK 平台的 OpenAI 兼容接口接入，无需自建推理服务。

### 3. Monkey-patch 修复 LLM 输出

GLM-5.1 偶尔输出未加引号的 JSON 参数（`{"license_plate": A12345}`），导致 LiveKit 框架解析失败。在 `llm_agent.py` 中 patch `prepare_function_arguments`，解析失败时自动修复引号。

**原因**：无法修改模型输出行为，也无法修改 LiveKit 框架源码，patch 是最小侵入的修复方式。

### 4. 工具工厂模式（create_voice_tools）

`voice_tool.py` 用闭包工厂将 `CallState` 绑定到 function_tool，而非全局变量或类方法。

**原因**：LiveKit `@function_tool` 要求是模块级函数，无法直接访问 Agent 实例状态。工厂模式让每个通话拥有独立的状态闭包，避免并发通话间状态串扰。

### 5. 微信推送用 PushPlus

**原因**：个人使用场景，无需企业微信认证。PushPlus 关注公众号即用，HTTP POST 一步到位，免费额度 200 次/天远超门卫场景需求。

### 6. 回访预填 + 开场确认

回访用户：查 DB → 预填历史字段到 CallState → 开场直接确认"还是和上次一样？"

**原因**：3 轮 15 秒的硬约束下，回访用户如果逐字段重采必定超时。预填 + 确认可将回访对话压缩到 1-2 轮。

## 环境搭建

```shell
# 创建虚拟环境
py -3 -m venv venv
source venv/Scripts/activate   # Windows PowerShell: venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt
```

## 启动

```shell
# 终端 1：API 服务（端口 8090）
python cli_main.py

# 终端 2：Agent Worker
python agents/llm_agent.py dev
```

## 环境变量

在 `.env.local` 中配置（已被 gitignore）。
| 变量                            | 必填 | 说明 |
|-------------------------------|----|------|
| `LIVEKIT_URL`                 | 是 | LiveKit 服务地址 |
| `LIVEKIT_API_KEY`             | 是 | LiveKit API Key |
| `LIVEKIT_API_SECRET`          | 是 | LiveKit API Secret |
| `VOLCENGINE_STT_APP_ID`       | 是 | 火山引擎 STT 应用 ID |
| `VOLCENGINE_STT_ACCESS_TOKEN` | 是 | 火山引擎访问令牌 |
| `VOLCENGINE_TTS_APP_ID`       | 是 | 火山引擎 TTS 应用 ID |
| `VOLCENGINE_TTS_ACCESS_TOKEN` | 是 | 火山引擎访问令牌 |
| `VOLCENGINE_LLM_API_KEY`      | 是 | 火山方舟 API Key |
| `PUSHPLUS_TOKEN`              | 否 | PushPlus 推送 Token（不填则跳过微信推送） |
| `MYSQL_HOST`                  | 否 | 默认 localhost |
| `MYSQL_DATABASE`              | 否 | 默认 mibo |
| `MYSQL_USER`                  | 否 | 默认 root |
| `MYSQL_PASSWORD`              | 否 | 默认空 |

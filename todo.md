# 豆包（火山引擎）STT/TTS 集成到 LiveKit Agents 实施方案

> 创建时间：2026-05-04
> 状态：待实施

---

## 一、背景

当前项目使用 LiveKit Agents 框架驱动工业园区访客呼入 Agent，语音管道配置为：

| 环节 | 当前方案 | 问题 |
|------|---------|------|
| STT | Deepgram nova-3 (zh-CN) | 中文识别效果一般，非国产模型 |
| TTS | Cartesia sonic-3 (zh) | 中文语音自然度一般，非国产模型 |

**目标**：将 STT 和 TTS 替换为豆包（火山引擎）的模型，提升中文场景的识别/合成效果。

**约束**：LiveKit Agents 目前无官方火山引擎插件，需自行实现适配。

---

## 二、方案选型结论：自定义 STT/TTS 插件

### 为什么不用代理层

代理层（策略二）在请求链路上多一跳，实时语音通话场景下延迟敏感，不可接受。

### 为什么不用混合替换

如果最终两个都要换，不如一步到位，减少重复工作。

### 自定义插件的优势

- 直接融入 LiveKit AgentSession 管道，零额外延迟
- 火山引擎 ASR/TTS 均提供 WebSocket 流式接口，与 LiveKit 插件模型天然匹配
- 独立 Python 包，可复用、可开源
- 一次投入，后续稳定运行

---

## 三、架构设计

```
┌─────────────────────────────────────────────────┐
│              LiveKit AgentSession                │
│                                                  │
│  Audio In ──▶ VolcengineSTT ──▶ LLM ──▶ VolcengineTTS ──▶ Audio Out
│                  (STT)              (TTS)         │
└──────────┬───────────────────────────────────────┘
           │ WebSocket            │ WebSocket
           ▼                     ▼
  ┌─────────────────┐   ┌─────────────────┐
  │ 火山引擎 ASR     │   │ 火山引擎 TTS     │
  │ (流式语音识别)    │   │ (流式语音合成)    │
  └─────────────────┘   └─────────────────┘
```

### 文件结构

```
livekit_plugins_volcengine/
├── __init__.py              # 导出 VolcengineSTT, VolcengineTTS
├── stt.py                   # STT 插件实现
├── tts.py                   # TTS 插件实现
├── auth.py                  # 火山引擎鉴权工具
└── proto/                   # 协议常量 / 消息格式定义
    └── asr_protocol.py      # ASR WebSocket 协议消息构造
    └── tts_protocol.py      # TTS WebSocket 协议消息构造
```

---

## 四、VolcengineSTT 实现

### 4.1 火山引擎 ASR 接口

- **服务**：火山引擎语音识别（大模型版 / 流式版）
- **协议**：WebSocket 全双工
- **鉴权方式**：HTTP Header 直传密钥（非 JWT）
  - ASR：WebSocket 握手时在 HTTP Header 传 `X-Api-Access-Key` + `X-Api-App-Key` + `X-Api-Resource-Id`
  - TTS：WebSocket 握手时 HTTP Header 传 `Authorization: Bearer; {access_token}`，请求体传 `appid` + `token` + `cluster`
- **输入**：PCM 16bit 16kHz 单声道音频流
- **输出**：中间结果（partial）+ 最终结果（final）+ 标点恢复

### 4.2 核心实现逻辑

```python
# stt.py 骨架
from livekit.agents.stt import STT, SpeechEvent, SpeechEventType, STTCloseEvent
from livekit.agents.utils import AudioStream

class VolcengineSTT(STT):
    def __init__(
        self,
        *,
        access_key: str,       # X-Api-Access-Key
        app_key: str,          # X-Api-App-Key
        resource_id: str = "volc.bigasr.sauc.duration",
        language: str = "zh-CN",
        sample_rate: int = 16000,
    ):
        super().__init__(
            capabilities=STTCapabilities(streaming=True, interim_results=True)
        )
        self._access_key = access_key
        self._app_key = app_key
        self._resource_id = resource_id
        self._language = language
        self._sample_rate = sample_rate

    async def _recognize_impl(self, buffer: AudioBuffer) -> SpeechEvent:
        """单次识别（非流式），用于短音频场景"""
        # 调用火山引擎一句话识别 HTTP API
        ...

    def stream(self) -> SpeechStream:
        """流式识别，用于实时通话场景"""
        return VolcengineSTTStream(self)


class VolcengineSTTStream(SpeechStream):
    """火山引擎流式 ASR 适配器

    内部维护一个 WebSocket 连接到火山引擎 ASR 服务，
    将 LiveKit 的音频帧写入 WebSocket，将识别结果推回管道。
    """

    async def _run(self):
        async with websockets.connect(ASR_WS_URL, ...) as ws:
            # 1. 发送开始帧（含鉴权、配置）
            # 2. 持续接收音频帧，写入 WebSocket
            # 3. 持续读取识别结果，转换为 SpeechEvent 推送
            # 4. 音频结束后发送结束帧
            ...
```

### 4.3 关键细节

| 项目 | 说明 |
|------|------|
| 鉴权 | WebSocket 握手时在 HTTP Header 传 `X-Api-Access-Key` + `X-Api-App-Key` + `X-Api-Resource-Id`，无需 JWT |
| 音频格式 | LiveKit 默认 48kHz，需重采样到 16kHz 后发送 |
| 中间结果 | `is_final=False` 的结果作为 `INTERIM_TRANSCRIPT` 推送 |
| 最终结果 | `is_final=True` 的结果作为 `FINAL_TRANSCRIPT` 推送 |
| 断句 | 利用 ASR 返回的 `result_index` 区分不同句子 |
| 错误恢复 | WebSocket 断连时自动重连，重连后重新发送鉴权帧 |
| VAD 配合 | LiveKit 的 Silero VAD 负责端点检测，STT 只负责识别 |

---

## 五、VolcengineTTS 实现

### 5.1 火山引擎 TTS 接口

- **服务**：火山引擎语音合成（大模型版 / 流式版）
- **协议**：WebSocket 全双工
- **鉴权方式**：双重认证（HTTP Header + 请求体）
  - WebSocket 握手时 HTTP Header 传 `Authorization: Bearer; {access_token}`
  - 请求体 JSON 传 `appid` + `token` + `cluster`
  - 注意：Header 中 `Bearer;` 后是分号+空格，非标准 Bearer 格式
- **输入**：文本（SSML 可选）
- **输出**：PCM 16bit 24kHz/16kHz 音频流（分片返回）

### 5.2 核心实现逻辑

```python
# tts.py 骨架
from livekit.agents.tts import TTS, SynthesizedAudio, TTSCloseEvent

class VolcengineTTS(TTS):
    def __init__(
        self,
        *,
        access_token: str,     # 控制台生成的 Access Token
        appid: str,            # 应用 ID
        cluster: str,          # 语音生成组 ID
        voice_type: str = "zh_female_tianmeixiaoyuan",  # 豆包音色
        sample_rate: int = 24000,
        language: str = "zh",
    ):
        super().__init__(
            capabilities=TTSCapabilities(streaming=True)
        )
        self._access_token = access_token
        self._appid = appid
        self._cluster = cluster
        self._voice_type = voice_type
        self._sample_rate = sample_rate

    async def synthesize(self, text: str) -> AsyncIterator[SynthesizedAudio]:
        """单次合成（整句），用于短文本场景"""
        ...

    def stream(self) -> SynthesizeStream:
        """流式合成，用于 LLM 逐 token 输出场景"""
        return VolcengineTTSStream(self)


class VolcengineTTSStream(SynthesizeStream):
    """火山引擎流式 TTS 适配器

    将 LLM 输出的文本 token 逐个喂入，缓冲到合适长度后
    发送至火山引擎 TTS WebSocket，接收合成音频流。
    """

    async def _run(self):
        async with websockets.connect(TTS_WS_URL, ...) as ws:
            # 1. 发送开始帧（含鉴权、音色配置）
            # 2. 监听 token 输入，缓冲/按标点切分后发送文本段
            # 3. 持续读取合成音频帧，转换为 SynthesizedAudio 推送
            # 4. 文本结束后发送结束帧
            ...
```

### 5.3 关键细节

| 项目 | 说明 |
|------|------|
| 音色选择 | 豆包提供多种中文音色，推荐 `zh_female_tianmeixiaoyuan`（甜美）或 `zh_male_chunhou`（醇厚），可配置 |
| 文本切分 | LLM 逐 token 输出，需按标点（句号、逗号、问号）切分后发送，避免合成质量下降 |
| 音频格式 | 火山引擎返回 PCM 16bit，需匹配 LiveKit 期望的采样率（48kHz），需重采样 |
| 流式延迟 | 首包延迟约 200-300ms，优于 HTTP 非流式方案 |
| SSML | 可选支持，用于控制语速、停顿等 |
| 并发 | 同一 WebSocket 连接串行处理，高并发需连接池 |

---

## 六、鉴权模块

火山引擎语音服务**不使用 JWT**，鉴权方式因 ASR 和 TTS 而异：

### 6.1 ASR 鉴权（流式语音识别）

WebSocket 握手时通过 HTTP Header 直传密钥：

```python
# ASR 鉴权 — 无需 JWT，无需额外获取 Token
def _build_asr_headers(self) -> dict:
    return {
        "X-Api-Access-Key": self._access_key,       # 控制台获取
        "X-Api-App-Key": self._app_key,              # 控制台获取（即 appid）
        "X-Api-Resource-Id": self._resource_id,       # 固定值 volc.bigasr.sauc.duration
        "X-Api-Request-Id": str(uuid.uuid4()),        # 每次请求唯一
    }

# 连接示例
ws = await websockets.connect(
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
    extra_headers=self._build_asr_headers(),
)
```

### 6.2 TTS 鉴权（流式语音合成）

双重认证：WebSocket 握手 Header + 请求体 JSON：

```python
# TTS 鉴权 — 也无需 JWT
# 1. WebSocket 连接时 Header 传 Token
header = {"Authorization": f"Bearer; {self._access_token}"}  # 注意分号+空格

# 2. 请求体 JSON 传 appid + token + cluster
request_json = {
    "app": {
        "appid": self._appid,           # 应用 ID
        "token": self._access_token,     # Access Token（同 Header 中的 token）
        "cluster": self._cluster,        # 语音生成组 ID
    },
    "user": {...},
    "audio": {...},
    "request": {...},
}

# 连接示例
ws = await websockets.connect(
    "wss://openspeech.bytedance.com/api/v1/tts/ws_binary",
    extra_headers=header,
)
```

### 6.3 凭据获取

所有凭据均从火山引擎控制台直接获取，无需自行签发：

| 凭据 | 获取方式 | 用途 |
|------|---------|------|
| `Access Key` (ASR) | 控制台 → 语音识别 → 应用管理 | ASR WebSocket Header |
| `App Key` (ASR) | 同上 | ASR WebSocket Header（即 appid） |
| `Access Token` (TTS) | 控制台 → 语音合成 → 应用管理 | TTS Header + 请求体 |
| `App ID` (TTS) | 同上 | TTS 请求体 |
| `Cluster` (TTS) | 同上 | TTS 请求体 |

---

## 七、集成改造（现有代码修改）

### 7.1 AgentSession 初始化替换

**改前**（当前代码）：

```python
from livekit.plugins import deepgram, cartesia, silero
from livekit.plugins.openai import LLM as OpenAILLM

session = AgentSession(
    turn_detection=MultilingualModel(),
    vad=silero.VAD.load(),
    stt=deepgram.STT(language="zh-CN", model="nova-3"),
    tts=cartesia.TTS(language="zh", model="sonic-3"),
    llm=OpenAILLM(model="gpt-4o", temperature=0.7),
    min_endpointing_delay=1.5,
)
```

**改后**：

```python
from livekit.plugins import silero
from livekit.plugins.openai import LLM as OpenAILLM
from livekit_plugins_volcengine import VolcengineSTT, VolcengineTTS

session = AgentSession(
    turn_detection=MultilingualModel(),
    vad=silero.VAD.load(),
    stt=VolcengineSTT(
        access_key=os.getenv("VOLCENGINE_ASR_ACCESS_KEY"),
        app_key=os.getenv("VOLCENGINE_ASR_APP_KEY"),
        resource_id="volc.bigasr.sauc.duration",
        language="zh-CN",
    ),
    tts=VolcengineTTS(
        access_token=os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN"),
        appid=os.getenv("VOLCENGINE_TTS_APP_ID"),
        cluster=os.getenv("VOLCENGINE_TTS_CLUSTER"),
        voice_type=os.getenv("VOLCENGINE_TTS_VOICE", "zh_female_tianmeixiaoyuan"),
    ),
    llm=OpenAILLM(model="gpt-4o", temperature=0.7),
    min_endpointing_delay=1.5,
)
```

### 7.2 环境变量

在 `.env.local` 中新增：

```env
# 火山引擎 - ASR（流式语音识别）
VOLCENGINE_ASR_ACCESS_KEY=your_asr_access_key
VOLCENGINE_ASR_APP_KEY=your_asr_app_key

# 火山引擎 - TTS（流式语音合成）
VOLCENGINE_TTS_ACCESS_TOKEN=your_tts_access_token
VOLCENGINE_TTS_APP_ID=your_tts_app_id
VOLCENGINE_TTS_CLUSTER=your_tts_cluster
VOLCENGINE_TTS_VOICE=zh_female_tianmeixiaoyuan
```

### 7.3 依赖新增

在 `requirements.txt` 或 `pyproject.toml` 中新增：

```
websockets>=12.0
```

---

## 八、实施步骤（TODO）

- [ ] **Step 1**：在火山引擎控制台开通语音识别 & 语音合成服务，获取各凭据（Access Key、App Key、Access Token、App ID、Cluster）
- [ ] **Step 2**：创建 `livekit_plugins_volcengine/` 包目录结构
- [ ] **Step 3**：实现 `auth.py` — 封装 ASR/TTS 鉴权参数构建（Header 构造，非 JWT）
- [ ] **Step 4**：实现 `stt.py` — VolcengineSTT 插件
  - [ ] 4.1 实现非流式识别（`_recognize_impl`）
  - [ ] 4.2 实现流式识别（`VolcengineSTTStream`）
  - [ ] 4.3 音频重采样（48kHz → 16kHz）
  - [ ] 4.4 WebSocket 断连重连
- [ ] **Step 5**：实现 `tts.py` — VolcengineTTS 插件
  - [ ] 5.1 实现非流式合成（`synthesize`）
  - [ ] 5.2 实现流式合成（`VolcengineTTSStream`）
  - [ ] 5.3 文本按标点切分逻辑
  - [ ] 5.4 音频重采样（24kHz → 48kHz）
  - [ ] 5.5 音色配置与切换
- [ ] **Step 6**：修改 `inbound_agent.py`，替换 STT/TTS 为火山引擎插件
- [ ] **Step 7**：新增 `.env.local` 环境变量配置
- [ ] **Step 8**：本地联调测试
  - [ ] 8.1 测试 STT：中文语音识别准确率
  - [ ] 8.2 测试 TTS：中文语音合成自然度
  - [ ] 8.3 测试端到端：完整通话流程
  - [ ] 8.4 测试异常：网络断连、Token 过期、并发
- [ ] **Step 9**：性能优化（延迟、内存、连接池）
- [ ] **Step 10**：文档 & 代码清理

---

## 九、风险与注意事项

| 风险 | 影响 | 应对 |
|------|------|------|
| 火山引擎 WebSocket 协议变更 | 适配代码需修改 | 关注火山引擎 API 变更通知，做好版本锁定 |
| Token 过期导致连接断开 | 通话中断 | ASR 不涉及 Token 过期；TTS Access Token 需关注有效期，建议启动时校验有效性 |
| 音频重采样引入延迟 | 通话延迟增加 | 使用 libsamplerate 或 numpy 高效重采样，控制 < 10ms |
| 火山引擎服务可用性 | 通话不可用 | 实现降级策略：失败时回退到 Deepgram/Cartesia |
| 并发连接数限制 | 高峰期连接失败 | 实现连接池，监控连接数，设置合理上限 |
| LLM 仍然使用 OpenAI | 整体非纯国产 | 后续可考虑替换为豆包 LLM（OpenAI 兼容接口，改动小） |

---

## 十、后续优化方向

1. **LLM 替换**：将 `OpenAILLM` 替换为豆包大模型（火山引擎提供 OpenAI 兼容接口，只需改 `base_url` 和 `api_key`，改动极小）
2. **音色 A/B 测试**：对多种豆包音色进行用户偏好测试
3. **降级策略完善**：STT/TTS 自动降级到备用供应商
4. **监控告警**：接入 Prometheus/Grafana，监控识别准确率、合成延迟、连接状态
5. **开源发布**：将 `livekit-plugins-volcengine` 作为独立包发布到 PyPI

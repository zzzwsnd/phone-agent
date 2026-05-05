"""
LiveKit 配置 — 从 .env.local 读取 LiveKit 连接参数、SIP 配置和火山引擎配置
"""
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

# ── LiveKit 服务连接 ─────────────────────────────────────────────────────────
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# ── Agent 名称（调度匹配用） ─────────────────────────────────────────────────
AGENT_NAME = os.getenv("AGENT_NAME", "outbound-caller")
# INBOUND_AGENT_NAME = os.getenv("INBOUND_AGENT_NAME", "park-visitor-agent")

# ── 火山引擎 STT ────────────────────────────────────────────────────────────
VOLCENGINE_STT_APP_ID = os.getenv("VOLCENGINE_STT_APP_ID", "")
VOLCENGINE_STT_CLUSTER = os.getenv("VOLCENGINE_STT_CLUSTER", "volcengine_streaming_common")

# ── 火山引擎 TTS ────────────────────────────────────────────────────────────
VOLCENGINE_TTS_APP_ID = os.getenv("VOLCENGINE_TTS_APP_ID", "")
VOLCENGINE_TTS_CLUSTER = os.getenv("VOLCENGINE_TTS_CLUSTER", "volcano_tts")

# ── 火山引擎 Access Token（STT/TTS 各自独立）─────────────────────────────────
VOLCENGINE_STT_ACCESS_TOKEN = os.getenv("VOLCENGINE_STT_ACCESS_TOKEN", "")
VOLCENGINE_TTS_ACCESS_TOKEN = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN", "")

# ── 火山方舟 LLM ────────────────────────────────────────────────────────────
VOLCENGINE_LLM_API_KEY = os.getenv("VOLCENGINE_LLM_API_KEY", "")
VOLCENGINE_LLM_MODEL = os.getenv("VOLCENGINE_LLM_MODEL", "glm-5.1")
VOLCENGINE_LLM_BASE_URL = os.getenv("VOLCENGINE_LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

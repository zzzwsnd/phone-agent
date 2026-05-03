"""
LiveKit 配置 — 从环境变量读取 LiveKit 连接参数和 SIP 配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── LiveKit 服务连接 ─────────────────────────────────────────────────────────
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# ── SIP 配置 ────────────────────────────────────────────────────────────────
SIP_OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
SIP_INBOUND_TRUNK_ID = os.getenv("SIP_INBOUND_TRUNK_ID", "")

# ── 可选 API Keys ────────────────────────────────────────────────────────────
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")

# ── Agent 名称（调度匹配用） ─────────────────────────────────────────────────
AGENT_NAME = os.getenv("AGENT_NAME", "outbound-caller")
INBOUND_AGENT_NAME = os.getenv("INBOUND_AGENT_NAME", "park-visitor-agent")

# ── 安全相关配置 ──────────────────────────────────────────────────────────────
SECURITY_TRANSFER_NUMBER = os.getenv("SECURITY_TRANSFER_NUMBER", "")

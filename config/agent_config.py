from livekit.agents import AgentSession
from livekit.plugins import volcengine, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import LLM as OpenAILLM
from config.livekit_config import (
    VOLCENGINE_STT_APP_ID, VOLCENGINE_STT_CLUSTER, VOLCENGINE_STT_ACCESS_TOKEN,
    VOLCENGINE_TTS_APP_ID, VOLCENGINE_TTS_CLUSTER, VOLCENGINE_TTS_ACCESS_TOKEN,
    VOLCENGINE_LLM_API_KEY, VOLCENGINE_LLM_MODEL, VOLCENGINE_LLM_BASE_URL,
)


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

"""
模型配置中心 — 所有 Agent 的 LLM 实例从这里导入

支持多种模型提供商：DeepSeek、豆包（Doubao）、通义千问（Qwen）等
API Key 从项目根目录的 .env.example 文件读取
"""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# ── 模型提供商配置 ────────────────────────────────────────────────────────────
# 从环境变量读取，支持切换不同提供商

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "deepseek")  # deepseek / doubao / qwen / openai

# 各提供商的 API 配置
PROVIDER_CONFIGS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": {
            "supervisor": "deepseek-chat",      # 强模型
            "fast": "deepseek-chat",            # 快模型
            "reasoning": "deepseek-reasoner",   # 推理模型
        }
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "DOUBAO_API_KEY",
        "models": {
            "supervisor": "doubao-pro-32k",
            "fast": "doubao-lite-32k",
            "reasoning": "doubao-pro-32k",
        }
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "models": {
            "supervisor": "qwen-max",
            "fast": "qwen-turbo",
            "reasoning": "qwen-plus",
        }
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": {
            "supervisor": "gpt-4-turbo-preview",
            "fast": "gpt-3.5-turbo",
            "reasoning": "gpt-4-turbo-preview",
        }
    },
}

# ── 初始化 LLM 实例 ───────────────────────────────────────────────────────────

config = PROVIDER_CONFIGS.get(MODEL_PROVIDER)
if not config:
    raise ValueError(f"不支持的模型提供商: {MODEL_PROVIDER}，支持的有: {list(PROVIDER_CONFIGS.keys())}")

api_key = os.getenv(config["api_key_env"])
if not api_key:
    raise ValueError(f"未配置 API Key: {config['api_key_env']}，请在 .env.example 文件中设置")

# Supervisor：复杂路由决策，用强模型
supervisor_llm = ChatOpenAI(
    model=config["models"]["supervisor"],
    base_url=config["base_url"],
    api_key=api_key,
    temperature=0.7,
)

# 选品 / 达人 / 内容策略：结构化数据处理为主，用快模型降低延迟和成本
fast_llm = ChatOpenAI(
    model=config["models"]["fast"],
    base_url=config["base_url"],
    api_key=api_key,
    temperature=0.3,
)

# 风险预警 / 效果评估：需要精准推理，用中等模型
reasoning_llm = ChatOpenAI(
    model=config["models"]["reasoning"],
    base_url=config["base_url"],
    api_key=api_key,
    temperature=0.5,
)

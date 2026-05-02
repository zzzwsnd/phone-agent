"""
FastAPI 服务启动入口

启动方式：
    python main.py                  # 启动 API 服务（端口 8000）
    python agents/llm_agent.py dev  # 启动 Agent Worker（另开终端）
"""
import logging
import uvicorn
from api.pthon_api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8090)

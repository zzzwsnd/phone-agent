"""
FastAPI 服务启动入口

启动方式：
    python main.py                  # 启动 API 服务（端口 8090）
    python agents/llm_agent.py dev  # 启动 Agent Worker（另开终端）
"""
import logging
import uvicorn
from api.pthon_api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

logger = logging.getLogger("park-visitor.main")


@app.on_event("startup")
async def startup():
    """启动时初始化数据库表"""
    try:
        from infra.schema import create_tables
        create_tables()
        logger.info("数据库表初始化完成")
    except Exception as e:
        logger.warning(f"数据库表初始化失败（如未配置 MySQL 可忽略）: {e}")


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8090)

"""
API 层 — FastAPI 路由，提供外呼调度和状态查询接口
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config.livekit_config import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, AGENT_NAME

# 需要的 import
import json
logger = logging.getLogger("outbound-caller.api")

app = FastAPI(title="外呼智能体 API", version="0.1.0")


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────

class CallRequest(BaseModel):
    """发起外呼请求"""
    phone_number: str = Field(..., description="被叫电话号码，如 +8613800138000")
    transfer_to: Optional[str] = Field(None, description="转接目标号码")
    customer_name: Optional[str] = Field("患者", description="客户姓名")
    appointment_time: Optional[str] = Field("待确认", description="预约时间")


class CallResponse(BaseModel):
    """外呼请求响应"""
    room_name: str
    status: str
    message: str


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    livekit_url: str


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    return HealthResponse(
        status="ok",
        livekit_url=LIVEKIT_URL,
    )


@app.post("/call", response_model=CallResponse)
async def create_call(req: CallRequest):
    """发起外呼

    通过 LiveKit API 创建调度请求，触发 Agent Worker 拨打电话。
    也可通过 `lk dispatch create` CLI 命令达到同样效果。
    """
    try:
        from livekit import api as lk_api

        # 构建调度元数据
        metadata = {
            "phone_number": req.phone_number,
            "transfer_to": req.transfer_to or "",
            "customer_name": req.customer_name,
            "appointment_time": req.appointment_time,
        }

        # 创建 LiveKit API 客户端
        lk_api_client = lk_api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )

        # 创建房间和调度
        import uuid
        room_name = f"call-{uuid.uuid4().hex[:8]}"

        await lk_api_client.room.create_room(
            lk_api.CreateRoomRequest(name=room_name)
        )

        await lk_api_client.agent.dispatch(
            lk_api.RoomAgentDispatch(
                room_name=room_name,
                agent_name=AGENT_NAME,
                metadata=json.dumps(metadata) if 'json' in dir() else str(metadata),
            )
        )

        logger.info(f"外呼调度已创建: room={room_name}, phone={req.phone_number}")

        return CallResponse(
            room_name=room_name,
            status="dispatched",
            message=f"已调度外呼至 {req.phone_number}",
        )

    except Exception as e:
        logger.error(f"创建外呼失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))




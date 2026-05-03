"""
API 层 — FastAPI 路由，提供访客查询和外呼调度接口
"""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uuid
from livekit import api as lk_api

from config.livekit_config import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, AGENT_NAME

import json
logger = logging.getLogger("park-visitor.api")

app = FastAPI(title="工业园区访客登记 API", version="0.2.0")


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────

class CallRequest(BaseModel):
    """发起外呼请求（outbound-only，保留兼容）"""
    phone_number: str = Field(..., description="被叫电话号码，如 +8613800138000")
    transfer_to: Optional[str] = Field(None, description="转接目标号码")
    customer_name: Optional[str] = Field("访客", description="客户姓名")
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


class VisitorRecord(BaseModel):
    """单条访客记录"""
    id: int
    caller_number: str
    license_plate: Optional[str] = None
    visiting_company: Optional[str] = None
    visitor_phone: Optional[str] = None
    purpose: Optional[str] = None
    visitor_name: Optional[str] = None
    call_room_name: Optional[str] = None
    created_at: Optional[str] = None


class VisitorListResponse(BaseModel):
    """访客记录列表响应"""
    records: list[VisitorRecord]
    total: int


class VisitorLookupResponse(BaseModel):
    """按号码查询访客响应"""
    records: list[VisitorRecord]
    is_return_visit: bool


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    return HealthResponse(
        status="ok",
        livekit_url=LIVEKIT_URL,
    )


@app.post("/call", response_model=CallResponse, deprecated=True)
async def create_call(req: CallRequest):
    """发起外呼（outbound-only，已标记废弃）

    通过 LiveKit API 创建调度请求，触发 Agent Worker 拨打电话。
    呼入模式不需要此接口。
    """
    try:
        metadata = {
            "phone_number": req.phone_number,
            "transfer_to": req.transfer_to or "",
            "customer_name": req.customer_name,
            "appointment_time": req.appointment_time,
        }

        lk_api_client = lk_api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )

        room_name = f"call-{uuid.uuid4().hex[:8]}"

        await lk_api_client.room.create_room(
            lk_api.CreateRoomRequest(name=room_name)
        )

        await lk_api_client.agent.dispatch(
            lk_api.RoomAgentDispatch(
                room_name=room_name,
                agent_name=AGENT_NAME,
                metadata=json.dumps(metadata),
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


@app.get("/visitors", response_model=VisitorListResponse)
async def list_visitors(limit: int = 50, offset: int = 0):
    """列出访客记录"""
    try:
        from infra.visitor_db import list_visitors as db_list
        records = db_list(limit=limit, offset=offset)
        visitor_records = [
            VisitorRecord(
                id=r["id"],
                caller_number=r["caller_number"],
                license_plate=r.get("license_plate"),
                visiting_company=r.get("visiting_company"),
                visitor_phone=r.get("visitor_phone"),
                purpose=r.get("purpose"),
                visitor_name=r.get("visitor_name"),
                call_room_name=r.get("call_room_name"),
                created_at=str(r["created_at"]) if r.get("created_at") else None,
            )
            for r in records
        ]
        return VisitorListResponse(records=visitor_records, total=len(visitor_records))
    except Exception as e:
        logger.error(f"查询访客列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/visitors/{phone}", response_model=VisitorLookupResponse)
async def lookup_visitor(phone: str):
    """按主叫号码查询访客记录（回访识别）"""
    try:
        from infra.visitor_db import lookup_visitor_by_phone
        records = lookup_visitor_by_phone(phone)
        visitor_records = [
            VisitorRecord(
                id=r["id"],
                caller_number=r["caller_number"],
                license_plate=r.get("license_plate"),
                visiting_company=r.get("visiting_company"),
                visitor_phone=r.get("visitor_phone"),
                purpose=r.get("purpose"),
                visitor_name=r.get("visitor_name"),
                call_room_name=r.get("call_room_name"),
                created_at=str(r["created_at"]) if r.get("created_at") else None,
            )
            for r in records
        ]
        return VisitorLookupResponse(
            records=visitor_records,
            is_return_visit=len(records) > 0,
        )
    except Exception as e:
        logger.error(f"查询访客失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

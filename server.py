#!/usr/bin/env python3
"""
电商全场景智能体 · 后端 v2.1
新增：用户登录认证、售前语义匹配
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from agents.orchestrator import FullAgent
from agents.auth import login as auth_login, verify_token, init_users_table
from engine import abl_conflict

app = FastAPI(title="电商全场景智能体", version="2.1")

init_users_table()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

agent = FullAgent()


# ── 数据模型 ──────────────────────────────────────────

class StartRequest(BaseModel):
    text: str
    order: str = ""
    package_status: str = "unopened"

class EventRequest(BaseModel):
    session: str
    event: str
    payload: Optional[dict] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class AdjudicateRequest(BaseModel):
    conflict_id: str
    decision: str
    operator_id: str = "CSR001"
    notes: str = ""


# ── 认证 endpoints ──────────────────────────────────

@app.post("/api/login")
def handle_login(req: LoginRequest):
    result = auth_login(req.username, req.password)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@app.get("/api/verify")
def handle_verify(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = verify_token(authorization[7:])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期")
    return user


# ── 业务 endpoints ──────────────────────────────────

@app.get("/api/orders")
def list_orders():
    from engine.memory_store import get_db, init_mock_data
    db = get_db()
    init_mock_data(db)
    rows = db.execute(
        "SELECT order_id, user_id, product_name, product_category, price, "
        "purchase_date_days_ago, package_status, activated, status "
        "FROM orders ORDER BY order_id").fetchall()
    return [{"order_id": r[0], "user_id": r[1], "product_name": r[2],
             "product_category": r[3], "price": r[4],
             "purchase_date_days_ago": r[5], "package_status": r[6],
             "activated": bool(r[7]), "status": r[8]} for r in rows]


@app.post("/api/start")
def start_session(req: StartRequest):
    try:
        return agent.start(user_text=req.text, order_id=req.order,
                           package_status=req.package_status)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/event")
def handle_event(req: EventRequest):
    try:
        return agent.event(session_id=req.session, event_name=req.event,
                           payload=req.payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/status/{session_id}")
def get_status(session_id: str):
    try:
        return agent.status(session_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── ABL 冲突裁决 ──────────────────────────────────

@app.get("/api/conflicts")
def list_conflicts(status: str = "pending"):
    return {"conflicts": abl_conflict.load_conflicts(status=status),
            "stats": abl_conflict.get_stats()}


@app.post("/api/conflicts/adjudicate")
def adjudicate_conflict(req: AdjudicateRequest):
    ok = abl_conflict.adjudicate(req.conflict_id, req.decision,
                                  req.operator_id, req.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="conflict not found")
    return {"ok": True, "conflict_id": req.conflict_id, "decision": req.decision}


# ── 静态文件 ──────────────────────────────────────────

frontend_dir = Path(__file__).resolve().parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

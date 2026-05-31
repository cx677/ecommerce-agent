"""
订单追踪引擎 — 3 步 SOP：查订单→查物流→反馈状态。

子场景：查物流、修改地址、催单
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Mock 物流数据 ────────────────────────────────────────

LOGISTICS_MOCK = {
    "SF": {"name": "顺丰速运", "base_days": 2},
    "ZTO": {"name": "中通快递", "base_days": 3},
    "YTO": {"name": "圆通速递", "base_days": 3},
    "STO": {"name": "申通快递", "base_days": 4},
}

STATUS_MAP = {
    "pending": "待发货",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "returned": "已退回",
}


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class TrackSession:
    session_id: str
    operator_id: str
    order_id: str
    user_text: str
    current_step: str = "query_order"
    order: dict = field(default_factory=dict)
    logistics: dict = field(default_factory=dict)
    sub_intent: str = ""
    talk_scripts: list[dict] = field(default_factory=list)
    resolution: str = ""


@dataclass
class TrackResult:
    session: TrackSession
    talk_script: str
    next_step: str


# ── 引擎 ────────────────────────────────────────────────


class TrackEngine:

    def start(self, session_id: str, operator_id: str, order_id: str,
              user_text: str, sub_intent: str = "") -> TrackResult:
        s = TrackSession(
            session_id=session_id,
            operator_id=operator_id,
            order_id=order_id,
            user_text=user_text,
            sub_intent=sub_intent or _classify_sub(user_text),
        )
        return self._step_query_order(s)

    def continue_session(self, s: TrackSession, event: str = "CONTINUE") -> TrackResult:
        if event == "TERMINATE":
            s.current_step = "end"
            s.resolution = "track_cancelled"
            return TrackResult(session=s, talk_script="已结束", next_step="end")

        if s.current_step == "query_order":
            return self._step_check_logistics(s)
        elif s.current_step == "check_logistics":
            return self._step_feedback(s, event)
        elif s.current_step == "feedback":
            s.resolution = "track_complete"
            s.current_step = "end"
            return TrackResult(
                session=s,
                talk_script="还有其他需要帮您查询的吗？",
                next_step="end",
            )
        return TrackResult(session=s, talk_script="", next_step="end")

    def _step_query_order(self, s: TrackSession) -> TrackResult:
        s.current_step = "query_order"
        from engine.memory_store import get_db, get_order
        db = get_db()
        order = get_order(db, s.order_id)
        if not order:
            s.resolution = "order_not_found"
            return TrackResult(session=s, talk_script="未找到订单信息",
                              next_step="end")

        s.order = order
        logistics = order.get("logistics_status", {})
        if isinstance(logistics, str):
            try:
                logistics = json.loads(logistics)
            except Exception:
                logistics = {}
        s.logistics = logistics if logistics else {
            "company": order.get("logistics_company", "SF"),
            "tracking": order.get("tracking_number", ""),
            "status": order.get("logistics_status", "in_transit"),
            "estimated": order.get("estimated_delivery", ""),
            "location": order.get("current_location", ""),
        }

        script = f"已查到您的订单[{s.order_id}]，物流信息如下："
        s.talk_scripts.append({"step": "query_order", "script": script})
        return TrackResult(session=s, talk_script=script,
                          next_step="check_logistics")

    def _step_check_logistics(self, s: TrackSession) -> TrackResult:
        s.current_step = "check_logistics"

        company = s.logistics.get("company", "SF")
        status = s.logistics.get("status", "in_transit")
        tracking = s.logistics.get("tracking", "")
        location = s.logistics.get("location", "")
        estimated = s.logistics.get("estimated", "")

        co_info = LOGISTICS_MOCK.get(company, {"name": company})
        status_cn = STATUS_MAP.get(status, status)

        if s.sub_intent == "change_address":
            if status == "pending":
                script = (f"订单[{s.order_id}]尚未发货，可以修改地址。"
                         f"请提供新的收货地址，我帮您更新。")
            else:
                script = (f"订单[{s.order_id}]已发货（{status_cn}），"
                         f"无法直接修改地址。需要我帮您联系快递拦截吗？")
        elif s.sub_intent == "urge_delivery":
            if status == "delivered":
                script = f"订单[{s.order_id}]已显示签收，您收到了吗？"
            else:
                script = (f"已为您催促快递，当前状态：{status_cn}。"
                         f"预计{estimated}送达。")
        else:
            script = (f"📦 {co_info['name']} | 运单号：{tracking}\n"
                     f"状态：{status_cn} | 位置：{location}\n"
                     f"预计送达：{estimated}")

        s.talk_scripts.append({"step": "check_logistics", "script": script})
        return TrackResult(session=s, talk_script=script, next_step="feedback")

    def _step_feedback(self, s: TrackSession, event: str) -> TrackResult:
        s.current_step = "feedback"
        s.resolution = "track_complete"
        return TrackResult(
            session=s,
            talk_script="还有其他需要帮您查询的吗？",
            next_step="end",
        )


def _classify_sub(text: str) -> str:
    if any(kw in text for kw in ["改地址", "换地址", "地址错"]):
        return "change_address"
    if any(kw in text for kw in ["催", "快点", "赶紧"]):
        return "urge_delivery"
    return "check_logistics"

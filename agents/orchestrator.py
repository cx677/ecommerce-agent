"""
Agent 编排器 — 全场景主控（退货 + 售前 + 订单追踪 + 投诉升级）。

根据意图路由器分派到 4 个引擎：
  return_refund → state_machine.Engine
  presale       → presale_engine.PresaleEngine
  order_track   → order_track_engine.TrackEngine
  complaint     → complaint_engine.ComplaintEngine

支持多坐席 operator_id 贯穿全链路。
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.state_machine import Engine, Session, load_session
from engine.presale_engine import PresaleEngine, PresaleSession
from engine.order_track_engine import TrackEngine, TrackSession
from engine.complaint_engine import ComplaintEngine, ComplaintSession
from engine.memory_store import get_db, init_mock_data
from agents.routing_agent import route as router
from agents.talk_agent import generate_talk, _fallback_talk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / ".state"
SESSIONS_DIR = STATE_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


class FullAgent:
    """全场景坐席助手"""

    def __init__(self, operator_id: str = "CSR001"):
        self.operator_id = operator_id
        self.return_engine = Engine()
        self.presale_engine = PresaleEngine()
        self.track_engine = TrackEngine()
        self.complaint_engine = ComplaintEngine()
        db = get_db()
        init_mock_data(db)

    # ── 启动 ───────────────────────────────────────────

    def start(self, user_text: str, order_id: str = "",
              package_status: str = "unopened") -> dict:
        """统一入口：路由 → 分派到对应引擎"""
        route_result = router(user_text)
        scene = route_result.scene

        if scene == "return_refund":
            if not order_id:
                order_id = route_result.entities.get("order_id") or "ORD001"
            session = self.return_engine.start_session(
                operator_id=self.operator_id,
                order_id=order_id,
                user_text=user_text,
                package_status=package_status,
            )
            return self._return_summary(session, scene, route_result)

        elif scene == "presale":
            sid = _new_sid()
            result = self.presale_engine.start(sid, self.operator_id, user_text)
            _save_session(sid, "presale", result.session)
            return self._scene_summary("presale", sid, result.talk_script,
                                       result.session.current_step, route_result,
                                       {"matches": result.session.matches})

        elif scene == "order_track":
            if not order_id:
                order_id = route_result.entities.get("order_id", "ORD001")
            sid = _new_sid()
            result = self.track_engine.start(
                sid, self.operator_id, order_id, user_text,
                sub_intent=route_result.sub_intent,
            )
            _save_session(sid, "order_track", result.session)
            return self._scene_summary("order_track", sid, result.talk_script,
                                       result.session.current_step, route_result,
                                       {"order_id": order_id})

        elif scene == "complaint":
            sid = _new_sid()
            result = self.complaint_engine.start(
                sid, self.operator_id, user_text,
                sub_intent=route_result.sub_intent,
                order_id=order_id or route_result.entities.get("order_id", ""),
            )
            _save_session(sid, "complaint", result.session)
            return self._scene_summary("complaint", sid, result.talk_script,
                                       result.session.current_step, route_result, {})

        return {"error": "unknown_scene", "scene": scene}

    # ── 事件推进 ───────────────────────────────────────

    def event(self, session_id: str, event_name: str,
              payload: dict | None = None) -> dict:
        """统一事件入口"""
        payload = payload or {}
        meta = _load_meta(session_id)
        if not meta:
            return {"error": "session_not_found"}

        scene = meta.get("scene", "return_refund")

        if scene == "return_refund":
            session = load_session(session_id)
            session = self.return_engine.handle_event(
                session, event_name, payload)
            # RouteResult as simple dict for event path
            return self._return_summary(
                session, scene, {"sub_intent": ""})

        elif scene == "presale":
            s = _load_session_obj(session_id, PresaleSession)
            result = self.presale_engine.continue_session(
                s, event_name, payload)
            _save_session(session_id, "presale", result.session)
            return self._scene_summary("presale", session_id,
                                       result.talk_script, result.session.current_step,
                                       {"scene": "presale", "sub_intent": ""},
                                       {"matches": result.session.matches})

        elif scene == "order_track":
            s = _load_session_obj(session_id, TrackSession)
            result = self.track_engine.continue_session(s, event_name)
            _save_session(session_id, "order_track", result.session)
            return self._scene_summary("order_track", session_id,
                                       result.talk_script, result.session.current_step,
                                       {"scene": "order_track"}, {})

        elif scene == "complaint":
            s = _load_session_obj(session_id, ComplaintSession)
            result = self.complaint_engine.continue_session(s, event_name)
            _save_session(session_id, "complaint", result.session)
            return self._scene_summary("complaint", session_id,
                                       result.talk_script, result.session.current_step,
                                       {"scene": "complaint"}, {})

        return {"error": "unknown_scene"}

    def status(self, session_id: str) -> dict:
        """查询会话状态"""
        meta = _load_meta(session_id)
        if not meta:
            return {"error": "session_not_found"}

        scene = meta.get("scene", "return_refund")
        if scene == "return_refund":
            session = load_session(session_id)
            return self._return_summary(
                session, scene, {"scene": scene, "sub_intent": ""})
        else:
            payload = meta.get("payload", {})
            return {
                "session_id": session_id,
                "scene": scene,
                "current_step": meta.get("current_step", ""),
                "talk_script": {"script": meta.get("last_talk", "")},
                "payload": payload,
            }

    # ── 内部 ───────────────────────────────────────────

    def _return_summary(self, s: Session, scene: str, route) -> dict:
        talk = s.talk_scripts[-1] if s.talk_scripts else {}
        if isinstance(route, dict):
            sub_intent = route.get("sub_intent", "")
        else:
            sub_intent = getattr(route, "sub_intent", "")
        return {
            "session_id": s.session_id,
            "scene": scene,
            "sub_intent": sub_intent,
            "operator_id": s.operator_id,
            "current_node": s.current_node,
            "order": {
                "order_id": s.order.get("order_id", ""),
                "product_name": s.order.get("product_name", ""),
                "price": s.order.get("price", 0),
            },
            "intent": s.intent,
            "eligibility": s.eligibility,
            "refund": s.refund,
            "talk_script": talk,
            "resolution": s.resolution,
            "history": s.history,
        }

    def _scene_summary(self, scene: str, sid: str, talk: str,
                       step: str, route, extra: dict) -> dict:
        if isinstance(route, dict):
            sub_intent = route.get("sub_intent", "")
        else:
            sub_intent = getattr(route, "sub_intent", "")
        return {
            "session_id": sid,
            "scene": scene,
            "sub_intent": sub_intent,
            "operator_id": self.operator_id,
            "current_step": step,
            "talk_script": {"script": talk},
            **extra,
        }


# ── 持久化 ─────────────────────────────────────────────


def _new_sid() -> str:
    import uuid
    return str(uuid.uuid4())[:8]


def _save_session(sid: str, scene: str, obj) -> None:
    path = SESSIONS_DIR / f"{sid}.json"
    meta = {
        "session_id": sid,
        "scene": scene,
        "current_step": getattr(obj, "current_step", getattr(obj, "current_node", "")),
        "last_talk": getattr(obj, "talk_scripts", [{}])[-1].get("script", "")
                      if hasattr(obj, "talk_scripts") and obj.talk_scripts else "",
        "payload": {},
    }
    if scene == "presale":
        meta["payload"] = {
            "matches": getattr(obj, "matches", []),
            "needs": getattr(obj, "needs", {}),
        }
    elif scene == "order_track":
        meta["payload"] = {"order_id": getattr(obj, "order_id", "")}
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _load_meta(sid: str) -> dict | None:
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_session_obj(sid: str, cls):
    meta = _load_meta(sid)
    if not meta:
        raise FileNotFoundError(f"Session {sid} not found")
    # 恢复 current_step（不在 payload 中）
    kwargs = {k: v for k, v in meta.get("payload", {}).items()
              if k in cls.__dataclass_fields__}
    kwargs["current_step"] = meta.get("current_step", kwargs.get("current_step", ""))
    return cls(
        session_id=sid,
        operator_id="unknown",
        user_text="",
        **kwargs,
    )

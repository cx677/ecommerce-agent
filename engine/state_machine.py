"""
状态机引擎 — 5 步退货退款 SOP。

节点流程：
  start → query_order → classify_intent → check_eligibility → 
  calculate_refund → execute_return → end

每个节点明确：入场副作用、等待事件、跳转目标。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import rules_engine as re
from . import abl_conflict
from .memory_store import get_db, record_return, get_order

try:
    from agents.talk_agent import generate_talk
except ImportError:
    generate_talk = None

# ── 路径配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / ".state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR = STATE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
AUDIT_LOG = STATE_DIR / "audit.jsonl"


def now_cn() -> datetime:
    return datetime.now()


def _append_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── 会话数据结构 ──────────────────────────────────────────


@dataclass
class Session:
    session_id: str
    operator_id: str
    order_id: str
    user_text: str
    current_node: str = "start"
    history: list[str] = field(default_factory=list)
    order: dict = field(default_factory=dict)
    intent: dict = field(default_factory=dict)
    eligibility: dict = field(default_factory=dict)
    refund: dict = field(default_factory=dict)
    talk_scripts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: now_cn().isoformat())
    closed_at: str = ""
    resolution: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "order_id": self.order_id,
            "user_text": self.user_text,
            "current_node": self.current_node,
            "history": self.history,
            "order": self.order,
            "intent": self.intent,
            "eligibility": self.eligibility,
            "refund": self.refund,
            "talk_scripts": self.talk_scripts,
            "actions": self.actions,
            "started_at": self.started_at,
            "closed_at": self.closed_at,
            "resolution": self.resolution,
        }


def _session_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def save_session(s: Session) -> None:
    _session_path(s.session_id).write_text(
        json.dumps(s.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_session(sid: str) -> Session:
    path = _session_path(sid)
    if not path.exists():
        raise FileNotFoundError(f"Session {sid} not found")
    return Session(**json.loads(path.read_text(encoding="utf-8")))


def audit(event_type: str, session: Session, payload: dict | None = None) -> None:
    _append_jsonl(AUDIT_LOG, {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "session_id": session.session_id,
        "current_node": session.current_node,
        "operator_id": session.operator_id,
        "occurred_at": now_cn().isoformat(),
        "payload": payload or {},
    })


# ── 引擎 ──────────────────────────────────────────────────


class Engine:
    """5 步状态机引擎"""

    def __init__(self):
        self.db = get_db()

    # ── 会话创建 ───────────────────────────────────────
    def start_session(self, operator_id: str, order_id: str,
                      user_text: str, package_status: str = "unopened") -> Session:
        sid = str(uuid.uuid4())[:8]
        session = Session(
            session_id=sid,
            operator_id=operator_id,
            order_id=order_id,
            user_text=user_text,
        )
        audit("SESSION_CREATED", session, {"order_id": order_id})
        self._transition(session, "query_order", {"package_status": package_status})
        save_session(session)
        return session

    # ── 事件分发 ───────────────────────────────────────
    def handle_event(self, session: Session, event: str,
                     payload: dict | None = None) -> Session:
        payload = payload or {}
        audit(event, session, payload)
        handler = getattr(self, f"_on_{session.current_node}", None)
        if handler is None:
            raise ValueError(f"No handler for node {session.current_node}")
        handler(session, event, payload)
        save_session(session)
        return session

    # ── 节点转换 ───────────────────────────────────────
    def _transition(self, session: Session, new_node: str,
                    payload: dict | None = None) -> None:
        session.history.append(session.current_node)
        session.current_node = new_node
        self._on_enter(session, new_node, payload or {})

    def _on_enter(self, session: Session, node: str, payload: dict) -> None:
        """入场副作用"""
        if node == "query_order":
            order = get_order(self.db, session.order_id)
            if not order:
                session.resolution = "order_not_found"
                self._transition(session, "end")
                return
            if "package_status" in payload:
                order["package_status"] = payload["package_status"]
            else:
                order.setdefault("package_status", "unopened")
            session.order = order

        elif node == "classify_intent":
            result = re.classify_intent(session.user_text)
            session.intent = {
                "intent_id": result.intent_id,
                "intent_name": result.intent_name,
                "confidence": result.confidence,
                "default_action": result.default_action,
            }
            candidates = []
            for iid, cfg in (re.RULES.get("intent_categories", {}).items()):
                if iid != result.intent_id:
                    candidates.append({"id": iid, "name": cfg.get("name", "")})
            session.intent["candidates"] = candidates

        elif node == "check_eligibility":
            er = re.check_eligibility(session.order, {
                "package_status": session.order.get("package_status", "unopened"),
            })
            session.eligibility = {
                "eligible": er.eligible,
                "escalate": er.escalate,
                "depreciation": er.depreciation,
                "failed_reasons": er.failed_reasons(),
            }
            checks_detail = []
            for c in er.checks:
                checks_detail.append({
                    "rule_id": c.rule_id,
                    "passed": c.passed,
                    "reason": c.reason,
                })
            session.eligibility["checks"] = checks_detail

        elif node == "calculate_refund":
            rr = re.calculate_refund(
                session.order,
                re.IntentResult(
                    intent_id=session.intent.get("intent_id", ""),
                    intent_name=session.intent.get("intent_name", ""),
                    default_action=session.intent.get("default_action", ""),
                ),
                re.EligibilityResult(
                    eligible=session.eligibility.get("eligible", False),
                    escalate=session.eligibility.get("escalate", False),
                    depreciation=session.eligibility.get("depreciation", False),
                ),
            )
            session.refund = {
                "approved": rr.approved,
                "refund_amount": rr.refund_amount,
                "action": rr.action,
                "shipping": rr.shipping,
                "notes": rr.notes,
                "reason": rr.reason,
            }

        elif node == "execute_return":
            pass  # 等待 CONFIRM_ACTION

        elif node == "end":
            session.closed_at = now_cn().isoformat()
            try:
                record_return(session)
            except Exception:
                pass

        # 话术生成：中间节点用模板（快），终点调 LLM
        if node in ("end", "execute_return"):
            self._emit_talk(session, node)
        else:
            self._emit_talk_fast(session, node)

    # ── 各节点事件处理 ─────────────────────────────────

    def _emit_talk_fast(self, session: Session, node: str) -> None:
        """中间节点快速模板话术（不调 LLM）"""
        from agents.talk_agent import _fallback_talk
        script = _fallback_talk({
            "current_node": node,
            "user_text": session.user_text,
            "order": session.order,
            "intent": session.intent,
            "eligibility": session.eligibility,
            "refund": session.refund,
            "resolution": session.resolution,
        })
        session.talk_scripts.append({"node": node, "script": script})

    def _emit_talk(self, session: Session, node: str) -> None:
        """生成话术（LLM优先，降级到模板）"""
        if generate_talk:
            try:
                script = generate_talk({
                    "current_node": node,
                    "order": session.order,
                    "intent": session.intent,
                    "eligibility": session.eligibility,
                    "refund": session.refund,
                    "resolution": session.resolution,
                })
                if script:
                    session.talk_scripts.append({"node": node, "script": script})
                    return
            except Exception:
                pass

        # 降级：硬编码模板
        from agents.talk_agent import _fallback_talk
        script = _fallback_talk({
            "current_node": node,
            "user_text": session.user_text,
            "order": session.order,
            "intent": session.intent,
            "eligibility": session.eligibility,
            "refund": session.refund,
            "resolution": session.resolution,
        })
        session.talk_scripts.append({"node": node, "script": script})

    def _on_start(self, s: Session, ev: str, pl: dict):
        if ev == "INPUT_SUBMITTED":
            self._transition(s, "query_order")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_query_order(self, s: Session, ev: str, pl: dict):
        if ev == "CONTINUE":
            self._transition(s, "classify_intent")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_classify_intent(self, s: Session, ev: str, pl: dict):
        if ev == "USER_ACCEPTED":
            self._transition(s, "check_eligibility")
        elif ev == "INTENT_SELECTED":
            sel = pl.get("selected_intent")
            if sel:
                for iid, cfg in (re.RULES.get("intent_categories", {}).items()):
                    if iid == sel:
                        s.intent["intent_id"] = sel
                        s.intent["intent_name"] = cfg.get("name", "")
                        s.intent["default_action"] = cfg.get("default_action", "")
                        break
                self._transition(s, "check_eligibility")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_check_eligibility(self, s: Session, ev: str, pl: dict):
        if ev == "CONTINUE":
            if s.eligibility.get("eligible"):
                self._transition(s, "calculate_refund")
            elif s.eligibility.get("escalate"):
                # ABL: 升级场景但用户情绪极强 → 记录冲突
                if s.eligibility:
                    abl_conflict.record_conflict(
                        session_id=s.session_id,
                        operator_id=s.operator_id,
                        order=s.order,
                        user_text=s.user_text,
                        eligibility=s.eligibility,
                    )
                self._transition(s, "end")
            else:
                # ABL: 规则判拒但用户情绪激烈 → 记录冲突到 JSONL
                if s.eligibility:
                    abl_conflict.record_conflict(
                        session_id=s.session_id,
                        operator_id=s.operator_id,
                        order=s.order,
                        user_text=s.user_text,
                        eligibility=s.eligibility,
                    )
                self._transition(s, "end")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_calculate_refund(self, s: Session, ev: str, pl: dict):
        if ev == "CONTINUE":
            if s.refund.get("approved"):
                self._transition(s, "execute_return")
            else:
                self._transition(s, "end")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_execute_return(self, s: Session, ev: str, pl: dict):
        if ev == "CONFIRM_ACTION":
            return_id = f"RTN{str(uuid.uuid4())[:8].upper()}"
            s.actions.append({
                "action_type": "return_created",
                "return_id": return_id,
                "refund_amount": s.refund.get("refund_amount"),
                "action": s.refund.get("action"),
                "idempotency_key": pl.get("idempotency_key", ""),
            })
            s.resolution = f"退货成功，退货单号{return_id}"
            s.talk_scripts.append({
                "node": "execute_return",
                "script": f"退货已办理成功！退货单号：{return_id}，"
                          f"退款金额：{s.refund.get('refund_amount')}元。"
                          f"退款将在3-5个工作日退回原支付方式。"
            })
            self._transition(s, "end")
        elif ev == "TERMINATE":
            self._transition(s, "end")

    def _on_end(self, s: Session, ev: str, pl: dict):
        pass  # 终端节点

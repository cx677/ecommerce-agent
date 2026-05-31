"""
投诉升级引擎 — 3 步 SOP：核实投诉→定责→补偿协商。

子场景：差评安抚、虚假宣传投诉、客服态度投诉
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 补偿规则 ────────────────────────────────────────────

COMPENSATION_RULES = {
    "false_advertising": {
        "description": "虚假宣传/商品与描述不符",
        "options": [
            {"action": "full_refund", "amount": "订单全额", "notes": "全额退款+免运费"},
            {"action": "partial_refund", "amount": "订单金额30%", "notes": "部分退款作为补偿"},
            {"action": "coupon", "amount": "50元优惠券", "notes": "发放补偿优惠券"},
        ],
        "escalate_if": "金额 > 500",
    },
    "poor_service": {
        "description": "客服态度差",
        "options": [
            {"action": "apology", "amount": "0", "notes": "诚恳致歉+内部追责"},
            {"action": "coupon", "amount": "20元优惠券", "notes": "发放安抚优惠券"},
        ],
        "escalate_if": "再次投诉",
    },
    "negative_review": {
        "description": "差评/不满意",
        "options": [
            {"action": "coupon", "amount": "10元优惠券", "notes": "发放安抚优惠券"},
            {"action": "return_refund", "amount": "退货退款", "notes": "走正常退货流程"},
        ],
        "escalate_if": "用户要求投诉升级",
    },
}


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class ComplaintSession:
    session_id: str
    operator_id: str
    user_text: str
    sub_intent: str = ""
    order_id: str = ""
    current_step: str = "verify"  # verify → determine → compensate
    complaint_type: str = ""
    order_amount: float = 0.0
    eligible: bool = True
    compensation: dict = field(default_factory=dict)
    talk_scripts: list[dict] = field(default_factory=list)
    resolution: str = ""


@dataclass
class ComplaintResult:
    session: ComplaintSession
    talk_script: str
    next_step: str


# ── 引擎 ────────────────────────────────────────────────


class ComplaintEngine:

    def start(self, session_id: str, operator_id: str, user_text: str,
              sub_intent: str = "", order_id: str = "",
              order_amount: float = 0.0) -> ComplaintResult:
        ctype = sub_intent or _classify_complaint_type(user_text)
        s = ComplaintSession(
            session_id=session_id,
            operator_id=operator_id,
            user_text=user_text,
            sub_intent=ctype,
            complaint_type=ctype,
            order_id=order_id,
            order_amount=order_amount,
        )
        return self._step_verify(s)

    def continue_session(self, s: ComplaintSession,
                         event: str = "CONTINUE") -> ComplaintResult:
        if event == "TERMINATE":
            s.current_step = "end"
            s.resolution = "complaint_cancelled"
            return ComplaintResult(session=s, talk_script="已结束", next_step="end")

        if s.current_step == "verify":
            return self._step_determine(s)
        elif s.current_step == "determine":
            return self._step_compensate(s, event)
        elif s.current_step == "compensate":
            s.resolution = "complaint_resolved"
            s.current_step = "end"
            return ComplaintResult(
                session=s,
                talk_script="感谢您的反馈，我们会持续改进服务。如有问题随时联系。",
                next_step="end",
            )
        return ComplaintResult(session=s, talk_script="", next_step="end")

    def _step_verify(self, s: ComplaintSession) -> ComplaintResult:
        s.current_step = "verify"
        rules = COMPENSATION_RULES.get(s.complaint_type, {})
        desc = rules.get("description", "客户投诉")

        script = f"已收到您的投诉（{desc}），正在核实情况。请稍候..."
        s.talk_scripts.append({"step": "verify", "script": script})
        return ComplaintResult(session=s, talk_script=script,
                              next_step="determine")

    def _step_determine(self, s: ComplaintSession) -> ComplaintResult:
        s.current_step = "determine"
        rules = COMPENSATION_RULES.get(s.complaint_type, {})

        # 判断是否需升级
        escalate_if = rules.get("escalate_if", "")
        if escalate_if and "金额" in escalate_if:
            if s.order_amount > 500:
                s.eligible = False
                script = ("经核实，此投诉涉及金额较大（¥{:.0f}），"
                         "已为您转接高级客服专员处理，预计24小时内回复。").format(s.order_amount)
                s.talk_scripts.append({"step": "determine", "script": script})
                return ComplaintResult(session=s, talk_script=script,
                                      next_step="end")

        # 确定补偿方案
        options = rules.get("options", [])
        if options:
            s.compensation = options[0]
        else:
            s.compensation = {"action": "apology", "amount": "0", "notes": "诚恳致歉"}

        script = f"经核实，{rules.get('description', '')}。为您提供以下方案：{s.compensation['notes']}"
        s.talk_scripts.append({"step": "determine", "script": script})
        return ComplaintResult(session=s, talk_script=script,
                              next_step="compensate")

    def _step_compensate(self, s: ComplaintSession, event: str) -> ComplaintResult:
        s.current_step = "compensate"
        if event == "USER_DECLINE_OFFER":
            script = "已为您升级至高级客服，将在24小时内联系您。"
            s.talk_scripts.append({"step": "compensate", "script": script})
            return ComplaintResult(session=s, talk_script=script, next_step="end")

        script = f"已为您办理：{s.compensation.get('notes','')}。感谢您的理解。"
        s.talk_scripts.append({"step": "compensate", "script": script})
        return ComplaintResult(session=s, talk_script=script, next_step="end")


def _classify_complaint_type(text: str) -> str:
    if any(kw in text for kw in ["虚假", "骗", "假货", "不符"]):
        return "false_advertising"
    if any(kw in text for kw in ["态度", "骂人", "服务差"]):
        return "poor_service"
    return "negative_review"

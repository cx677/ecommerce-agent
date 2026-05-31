"""
工具函数：退款金额计算。

输入：order_id, intent_id, package_status
输出：退款方案 JSON
"""

from ..engine.rules_engine import (
    calculate_refund, classify_intent, check_eligibility,
    IntentResult, EligibilityResult,
)
from ..engine.memory_store import get_db, get_order


def run(order_id: str, intent_id: str = "",
        package_status: str = "unopened") -> dict:
    """计算退款金额"""
    db = get_db()
    order = get_order(db, order_id)
    if not order:
        return {"approved": False, "error": "订单不存在"}

    # 意图
    if intent_id:
        intents = __import__("yaml", fromlist=["safe_load"]).safe_load(
            open(__import__("pathlib").Path(__file__).resolve().parent.parent /
                 "rules/return_rules.yaml", encoding="utf-8")
        ).get("intent_categories", {})
        cfg = intents.get(intent_id, {})
        intent = IntentResult(
            intent_id=intent_id,
            intent_name=cfg.get("name", ""),
            default_action=cfg.get("default_action", ""),
        )
    else:
        intent = IntentResult(
            intent_id="INTENT_04_NO_LONGER_WANTED",
            intent_name="不想要了",
            default_action="no_longer_wanted",
        )

    # 资格
    eligibility = check_eligibility(order, {"package_status": package_status})

    # 计算
    result = calculate_refund(order, intent, eligibility)
    return {
        "approved": result.approved,
        "refund_amount": result.refund_amount,
        "action": result.action,
        "shipping": result.shipping,
        "notes": result.notes,
        "reason": result.reason,
    }

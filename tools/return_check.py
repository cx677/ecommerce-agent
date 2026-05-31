"""
工具函数：退货资格预检。

输入：order_id, package_status
输出：资格判定结果 JSON
"""

from ..engine.rules_engine import check_eligibility
from ..engine.memory_store import get_db, get_order


def run(order_id: str, package_status: str = "unopened") -> dict:
    """预检退货资格（不产生副作用）"""
    db = get_db()
    order = get_order(db, order_id)
    if not order:
        return {"eligible": False, "error": "订单不存在"}

    result = check_eligibility(order, {"package_status": package_status})
    return {
        "eligible": result.eligible,
        "escalate": result.escalate,
        "depreciation": result.depreciation,
        "failed_reasons": result.failed_reasons(),
        "checks": [
            {"rule_id": c.rule_id, "passed": c.passed, "reason": c.reason}
            for c in result.checks
        ],
    }

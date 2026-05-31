#!/usr/bin/env python3
"""
电商全场景智能体 · MCP 接口层 v1.0

把查订单、退货预检、退款计算、ABL 冲突裁决暴露为标准 MCP tools，
供外部 Agent（Claude Desktop、Hermes 等）通过 stdio 调用。

启动（stdio 模式，供 MCP client 连接）：
  cd /Users/fjw/Desktop/电商/1 && /Users/fjw/.local/hermes-agent/.venv/bin/python3 mcp_server.py

HTTP 模式（调试用）：
  cd /Users/fjw/Desktop/电商/1 && /Users/fjw/.local/hermes-agent/.venv/bin/python3 mcp_server.py --transport streamable-http --port 8100

配置到 Claude Desktop 的 claude_desktop_config.json：
{
  "mcpServers": {
    "电商智能体": {
      "command": "/Users/fjw/.local/hermes-agent/.venv/bin/python3",
      "args": ["/Users/fjw/Desktop/电商/1/mcp_server.py"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根在 sys.path 中（engine/ 等模块可 import）
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "电商全场景智能体",
    json_response=True,
    instructions="电商客服工具集：查订单、退货预检、退款计算、ABL冲突裁决。金额单位人民币元。",
)

# ─────────────────────────────────────────────────────────
# 工具 1：查订单
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def order_query(order_id: str) -> dict:
    """查询订单详情。输入订单号(如ORD001)，返回商品名、品类、价格、购买天数、包裹状态等。

    示例：order_query(order_id="ORD001")
    """
    from engine.memory_store import get_db, get_order
    db = get_db()
    result = get_order(db, order_id)
    if result is None:
        return {"found": False, "error": f"订单 {order_id} 不存在"}
    return {"found": True, "order": result}


# ─────────────────────────────────────────────────────────
# 工具 2：退货资格预检
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def return_check(order_id: str, package_status: str = "unopened") -> dict:
    """预检一笔订单的退货资格（纯查询，不产生副作用）。

    package_status：unopened(未拆封) / opened_intact(已拆但完好) / damaged(破损)

    示例：return_check(order_id="ORD004", package_status="unopened")
    """
    from engine.rules_engine import check_eligibility
    from engine.memory_store import get_db, get_order
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


# ─────────────────────────────────────────────────────────
# 工具 3：退款金额计算
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def refund_calc(
    order_id: str,
    intent_id: str = "",
    package_status: str = "unopened",
) -> dict:
    """计算退货退款金额明细。需指定退货意图。

    常用 intent_id：
      INTENT_01_QUALITY    → 质量问题
      INTENT_02_DAMAGE     → 收到损坏
      INTENT_03_WRONG      → 发错货
      INTENT_04_NO_LONGER_WANTED → 不想要了
      INTENT_05_SIZE       → 尺码不合
      INTENT_06_TOO_LATE   → 太晚收到

    package_status：unopened / opened_intact / damaged

    返回 approved(refund_amount(action(shipping(notes。

    示例：refund_calc(order_id="ORD001", intent_id="INTENT_05_SIZE", package_status="unopened")
    """
    from engine.rules_engine import (
        calculate_refund, check_eligibility,
        IntentResult,
    )
    from engine.memory_store import get_db, get_order
    db = get_db()
    order = get_order(db, order_id)
    if not order:
        return {"approved": False, "error": "订单不存在"}

    # 解析意图
    if intent_id:
        try:
            intent_cfg = _load_intent_config(intent_id)
        except Exception:
            intent_cfg = {}
        intent = IntentResult(
            intent_id=intent_id,
            intent_name=intent_cfg.get("name", intent_id),
            default_action=intent_cfg.get("default_action", ""),
        )
    else:
        intent = IntentResult(
            intent_id="INTENT_04_NO_LONGER_WANTED",
            intent_name="不想要了",
            default_action="no_longer_wanted",
        )

    eligibility = check_eligibility(order, {"package_status": package_status})
    result = calculate_refund(order, intent, eligibility)
    return {
        "approved": result.approved,
        "refund_amount": result.refund_amount,
        "action": result.action,
        "shipping": result.shipping,
        "notes": result.notes,
        "reason": result.reason,
    }


def _load_intent_config(intent_id: str) -> dict:
    """从 return_rules.yaml 加载意图配置"""
    import yaml
    rules_path = PROJECT_ROOT / "rules" / "return_rules.yaml"
    if rules_path.exists():
        with open(rules_path, encoding="utf-8") as f:
            intents = yaml.safe_load(f).get("intent_categories", {})
            return intents.get(intent_id, {})
    return {}


# ─────────────────────────────────────────────────────────
# 工具 4：获取订单列表
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def order_list() -> dict:
    """获取系统中全部订单列表（最多20条），含订单号、商品名、品类、价格、购买天数等。"""
    from engine.memory_store import get_db, init_mock_data
    db = get_db()
    init_mock_data(db)
    rows = db.execute(
        "SELECT order_id, user_id, product_name, product_category, price, "
        "purchase_date_days_ago, package_status, activated, status "
        "FROM orders ORDER BY order_id"
    ).fetchall()
    orders = [
        {
            "order_id": r[0], "user_id": r[1], "product_name": r[2],
            "product_category": r[3], "price": r[4],
            "purchase_date_days_ago": r[5], "package_status": r[6],
            "activated": bool(r[7]), "status": r[8],
        }
        for r in rows
    ]
    return {"total": len(orders), "orders": orders}


# ─────────────────────────────────────────────────────────
# 工具 5：ABL 情绪检测
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def detect_emotion(user_text: str) -> dict:
    """检测用户文本的情绪强度。返回等级(none/mild/moderate/strong/extreme)、分数(0-4)、匹配关键词。

    分数>=2 表示情绪足够激烈，建议触发冲突记录。

    示例：detect_emotion(user_text="凭什么内衣不给退，我要投诉你们欺诈消费者")
    """
    from engine.abl_conflict import detect_emotion as _detect
    result = _detect(user_text)
    return {
        "has_emotion": result.has_emotion,
        "level": result.level,
        "score": result.score,
        "matched_keywords": result.matched_keywords,
    }


# ─────────────────────────────────────────────────────────
# 工具 6：ABL 冲突记录
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
)
def abl_record_conflict(
    session_id: str,
    order_id: str,
    user_text: str,
    operator_id: str = "MCP",
) -> dict:
    """规则判"不退"但用户情绪激烈时，自动检测情绪并记录冲突到 JSONL。

    工作流程：
    1. 查订单 + 预检退货资格
    2. 规则说"不" 且 用户情绪达 moderate 以上 → 记录冲突
    3. 返回冲突记录或跳过原因

    示例：abl_record_conflict(session_id="sess_001", order_id="ORD004",
           user_text="内衣凭什么不给退，我要投诉你们欺诈消费者")
    """
    from engine.memory_store import get_db, get_order
    from engine.rules_engine import check_eligibility
    from engine.abl_conflict import record_conflict

    db = get_db()
    order = get_order(db, order_id)
    if not order:
        return {"recorded": False, "error": f"订单 {order_id} 不存在"}

    eligibility_result = check_eligibility(order, {"package_status": order.get("package_status", "unopened")})
    eligibility = {
        "eligible": eligibility_result.eligible,
        "escalate": eligibility_result.escalate,
        "failed_reasons": eligibility_result.failed_reasons(),
        "checks": [{"rule_id": c.rule_id, "passed": c.passed, "reason": c.reason}
                   for c in eligibility_result.checks],
    }

    record = record_conflict(
        session_id=session_id,
        operator_id=operator_id,
        order=order,
        user_text=user_text,
        eligibility=eligibility,
    )

    if record is None:
        return {
            "recorded": False,
            "reason": "情绪未达阈值或规则已放行，未记录冲突",
            "eligible": eligibility.get("eligible"),
        }

    return {"recorded": True, "conflict": record.to_dict()}


# ─────────────────────────────────────────────────────────
# 工具 7：ABL 冲突查询
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
def abl_list_conflicts(status: str = "pending") -> dict:
    """查询 ABL 冲突记录。

    status：pending(待裁决) / resolved(已裁决) / uphold(维持) / override(放行) / escalate(升级) / all(全部)

    示例：abl_list_conflicts(status="pending")
    """
    from engine.abl_conflict import load_conflicts, get_stats
    return {
        "conflicts": load_conflicts(status=status),
        "stats": get_stats(),
    }


# ─────────────────────────────────────────────────────────
# 工具 8：ABL 人工裁决
# ─────────────────────────────────────────────────────────


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}
)
def abl_adjudicate(
    conflict_id: str,
    decision: str,
    operator_id: str = "MCP",
    notes: str = "",
) -> dict:
    """对一条待裁决 ABL 冲突进行人工判决。

    decision：uphold(维持原判，不退) / override(例外放行，允许退) / escalate(升级人工)

    示例：abl_adjudicate(conflict_id="a1b2c3d4", decision="override", notes="用户是VIP")
    """
    from engine.abl_conflict import adjudicate
    ok = adjudicate(conflict_id, decision, operator_id, notes)
    if not ok:
        return {"ok": False, "error": f"冲突 {conflict_id} 不存在"}
    return {"ok": True, "conflict_id": conflict_id, "decision": decision}


# ─────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="电商智能体 MCP Server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help="传输方式（默认 stdio）",
    )
    parser.add_argument("--port", type=int, default=8100, help="HTTP 模式端口")
    args = parser.parse_args()

    if args.transport == "streamable-http":
        print(f"📡 MCP Server 启动 (HTTP) → http://0.0.0.0:{args.port}/mcp")
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    else:
        mcp.run(transport="stdio")

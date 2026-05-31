"""
工具函数：查询订单详情。

输入：order_id
输出：订单 JSON 或 None
"""

from ..engine.memory_store import get_db, get_order as db_get_order


def run(order_id: str) -> dict | None:
    """查询订单"""
    db = get_db()
    return db_get_order(db, order_id)

"""
长期记忆层 — SQLite 存储。

- 订单数据（orders 表）
- 退货记录（returns 表）
- 客户画像（customer_profiles 表）

从 Mock JSONL 初始化，运行时持久化。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "ecommerce.db"
MOCK_DATA_PATH = PROJECT_ROOT / "data" / "orders.jsonl"


def get_db() -> sqlite3.Connection:
    """获取数据库连接（自动建表）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            product_category TEXT NOT NULL,
            price REAL NOT NULL,
            purchase_date TEXT NOT NULL,
            purchase_date_days_ago INTEGER DEFAULT 0,
            package_status TEXT DEFAULT 'unopened',
            activated INTEGER DEFAULT 0,
            status TEXT DEFAULT 'delivered',
            has_repeat_return_30d INTEGER DEFAULT 0,
            order_already_returned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS returns (
            return_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            session_id TEXT,
            operator_id TEXT,
            reason TEXT,
            intent_id TEXT,
            action TEXT,
            refund_amount REAL DEFAULT 0,
            shipping TEXT,
            resolution TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE IF NOT EXISTS customer_profiles (
            user_id TEXT PRIMARY KEY,
            total_returns INTEGER DEFAULT 0,
            last_return_date TEXT,
            total_refund_amount REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            price REAL DEFAULT 0,
            stock INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            external_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()


# ── 初始化 Mock 数据 ──────────────────────────────────────


def init_mock_data(conn: sqlite3.Connection | None = None) -> None:
    """从 orders.jsonl 导入 Mock 数据到 SQLite（幂等）"""
    if conn is None:
        conn = get_db()

    if not MOCK_DATA_PATH.exists():
        return

    with open(MOCK_DATA_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO orders 
                    (order_id, user_id, product_name, product_category, 
                     price, purchase_date, purchase_date_days_ago, 
                     package_status, activated, status,
                     has_repeat_return_30d, order_already_returned)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record["order_id"],
                    record["user_id"],
                    record["product_name"],
                    record["product_category"],
                    record["price"],
                    record.get("purchase_date", ""),
                    record.get("purchase_date_days_ago", 0),
                    record.get("package_status", "unopened"),
                    record.get("activated", 0),
                    record.get("status", "delivered"),
                    record.get("has_repeat_return_30d", False),
                    record.get("order_already_returned", False),
                ))
            except Exception:
                pass
    conn.commit()


# ── 查询接口 ──────────────────────────────────────────────


def get_order(conn: sqlite3.Connection, order_id: str) -> dict | None:
    """查询订单详情"""
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def record_return(session: Any) -> None:
    """把完成的退货会话写入数据库"""
    conn = get_db()
    return_id = ""
    for a in session.actions:
        if a.get("action_type") == "return_created":
            return_id = a.get("return_id", "")

    conn.execute("""
        INSERT INTO returns (return_id, order_id, session_id, operator_id,
            reason, intent_id, action, refund_amount, shipping, resolution)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        return_id or f"RTN{session.session_id.upper()}",
        session.order_id,
        session.session_id,
        session.operator_id,
        session.intent.get("intent_name", ""),
        session.intent.get("intent_id", ""),
        session.refund.get("action", ""),
        session.refund.get("refund_amount", 0),
        session.refund.get("shipping", ""),
        session.resolution,
    ))

    # 更新客户画像
    user_id = session.order.get("user_id", "")
    if user_id:
        conn.execute("""
            INSERT INTO customer_profiles (user_id, total_returns, last_return_date, total_refund_amount)
            VALUES (?, 1, datetime('now','localtime'), ?)
            ON CONFLICT(user_id) DO UPDATE SET
                total_returns = total_returns + 1,
                last_return_date = datetime('now','localtime'),
                total_refund_amount = total_refund_amount + ?,
                updated_at = datetime('now','localtime')
        """, (user_id, session.refund.get("refund_amount", 0),
              session.refund.get("refund_amount", 0)))

    # 标记订单已退货（幂等保护）
    conn.execute(
        "UPDATE orders SET order_already_returned = 1 WHERE order_id = ?",
        (session.order_id,)
    )
    conn.commit()


def get_customer_profile(conn: sqlite3.Connection, user_id: str) -> dict | None:
    """获取客户画像"""
    row = conn.execute(
        "SELECT * FROM customer_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_return_history(conn: sqlite3.Connection, order_id: str) -> list[dict]:
    """获取某订单的退货历史"""
    rows = conn.execute(
        "SELECT * FROM returns WHERE order_id = ? ORDER BY created_at DESC",
        (order_id,)
    ).fetchall()
    return [dict(r) for r in rows]

"""
平台同步引擎 — 从电商平台自动拉取商品和订单数据。

架构：
  PlatformAdapter (抽象接口)
    ├── TaobaoAdapter (淘宝开放平台)
    ├── JDAdapter (京东)
    └── CSVAdapter (本地文件)

用法:
  from engine.platform_sync import sync_products, sync_orders
  new_count = sync_products(adapter)
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.memory_store import get_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNC_LOG = PROJECT_ROOT / "data" / ".state" / "sync_log.jsonl"


def now_cn() -> datetime:
    return datetime.now()


# ── 抽象接口 ──────────────────────────────────────────────


class PlatformAdapter(ABC):
    """电商平台适配器抽象基类"""

    @abstractmethod
    def fetch_products(self, page: int = 1, page_size: int = 50) -> list[dict]:
        """拉取商品列表"""
        ...

    @abstractmethod
    def fetch_orders(self, start_time: str = "", end_time: str = "",
                     page: int = 1, page_size: int = 50) -> list[dict]:
        """拉取订单列表"""
        ...

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称"""
        ...


# ── 淘宝开放平台适配器 ────────────────────────────────────


class TaobaoAdapter(PlatformAdapter):
    """
    淘宝/天猫开放平台适配器。

    前置条件：
      1. 注册淘宝开放平台开发者 https://open.taobao.com
      2. 创建应用获得 AppKey 和 AppSecret
      3. 获取店铺授权 session_key

    API 文档：
      - 商品列表: taobao.items.onsale.get
      - 订单列表: taobao.trades.sold.get
    """

    API_URL = "https://eco.taobao.com/router/rest"
    SIGN_METHOD = "md5"
    FORMAT = "json"
    VERSION = "2.0"

    def __init__(self, app_key: str = "", app_secret: str = "",
                 session_key: str = ""):
        self.app_key = app_key or os.environ.get("TAOBAO_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("TAOBAO_APP_SECRET", "")
        self.session_key = session_key or os.environ.get("TAOBAO_SESSION_KEY", "")

    @property
    def platform_name(self) -> str:
        return "taobao"

    def _sign(self, params: dict) -> str:
        """生成淘宝 API 签名"""
        import hashlib
        sorted_params = sorted(params.items())
        sign_str = self.app_secret + "".join(
            f"{k}{v}" for k, v in sorted_params if v
        ) + self.app_secret
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

    def _call(self, method: str, extra: dict) -> dict:
        """调用淘宝 API"""
        import hashlib
        import time as _time

        if not all([self.app_key, self.app_secret, self.session_key]):
            return {"error": "缺少 API 凭证（app_key/app_secret/session_key）"}

        params = {
            "method": method,
            "app_key": self.app_key,
            "session": self.session_key,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": self.FORMAT,
            "v": self.VERSION,
            "sign_method": self.SIGN_METHOD,
            **extra,
        }
        params["sign"] = self._sign(params)

        try:
            import urllib.request
            import urllib.parse
            data = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(self.API_URL, data=data)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
                if "error_response" in body:
                    return {"error": body["error_response"].get("sub_msg", "API error")}
                return body
        except Exception as e:
            return {"error": str(e)[:200]}

    def fetch_products(self, page: int = 1, page_size: int = 50) -> list[dict]:
        result = self._call("taobao.items.onsale.get", {
            "fields": "num_iid,title,price,num,created,modified",
            "page_no": str(page),
            "page_size": str(min(page_size, 200)),
        })
        if "error" in result:
            _log_sync(self.platform_name, "products", f"error: {result['error']}")
            return []

        items = result.get("items_onsale_get_response", {}).get("items", {}).get("item", [])
        products = []
        for item in items if isinstance(items, list) else [items]:
            products.append({
                "product_id": f"TB{item.get('num_iid', '')}",
                "name": item.get("title", ""),
                "category": "",
                "price": float(item.get("price", 0)),
                "stock": int(item.get("num", 0)),
                "source": "taobao",
                "external_id": str(item.get("num_iid", "")),
            })
        return products

    def fetch_orders(self, start_time: str = "", end_time: str = "",
                     page: int = 1, page_size: int = 50) -> list[dict]:
        extra = {
            "fields": "tid,title,price,num,status,created,pay_time",
            "page_no": str(page),
            "page_size": str(min(page_size, 100)),
        }
        if start_time:
            extra["start_created"] = start_time
        if end_time:
            extra["end_created"] = end_time

        result = self._call("taobao.trades.sold.get", extra)
        if "error" in result:
            _log_sync(self.platform_name, "orders", f"error: {result['error']}")
            return []

        trades = result.get("trades_sold_get_response", {}).get("trades", {}).get("trade", [])
        orders = []
        for t in trades if isinstance(trades, list) else [trades]:
            orders.append({
                "order_id": f"TB{t.get('tid', '')}",
                "product_name": t.get("title", ""),
                "price": float(t.get("price", 0)),
                "status": t.get("status", ""),
                "created": t.get("created", ""),
                "source": "taobao",
            })
        return orders


# ── CSV 适配器 ────────────────────────────────────────────


class CSVAdapter(PlatformAdapter):
    """从本地 CSV 文件读取商品（用于离线/演示）"""

    def __init__(self, path: str = ""):
        self.path = path or str(PROJECT_ROOT / "data" / "products.csv")

    @property
    def platform_name(self) -> str:
        return "csv"

    def fetch_products(self, page: int = 1, page_size: int = 50) -> list[dict]:
        import csv
        if not Path(self.path).exists():
            return []
        products = []
        with open(self.path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                products.append({
                    "product_id": row.get("product_id", f"CSV{len(products)+1}"),
                    "name": row.get("name", ""),
                    "category": row.get("category", ""),
                    "price": float(row.get("price", 0)),
                    "stock": int(row.get("stock", 0)),
                    "source": "csv",
                })
        start = (page - 1) * page_size
        return products[start:start + page_size]

    def fetch_orders(self, **kwargs) -> list[dict]:
        return []


# ── 同步引擎 ──────────────────────────────────────────────


def sync_products(adapter: PlatformAdapter) -> int:
    """拉取商品 → 写入数据库。返回新增数量。"""
    db = get_db()
    existing = set(
        row[0] for row in
        db.execute("SELECT product_id FROM products").fetchall()
    )

    new_count = 0
    page = 1
    while True:
        items = adapter.fetch_products(page=page, page_size=50)
        if not items:
            break
        for item in items:
            pid = item["product_id"]
            if pid in existing:
                continue
            existing.add(pid)
            try:
                db.execute(
                    """INSERT INTO products
                       (product_id, name, category, price, stock, source, external_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pid, item["name"], item.get("category", ""),
                     item["price"], item.get("stock", 0),
                     item.get("source", adapter.platform_name),
                     item.get("external_id", "")),
                )
                new_count += 1
            except Exception:
                pass
        page += 1
        if len(items) < 50:
            break

    db.commit()
    _log_sync(adapter.platform_name, "products", f"synced: +{new_count}")
    return new_count


def sync_orders(adapter: PlatformAdapter,
                start_time: str = "", end_time: str = "") -> int:
    """拉取订单 → 写入数据库。返回新增数量。"""
    db = get_db()
    existing = set(
        row[0] for row in
        db.execute("SELECT order_id FROM orders").fetchall()
    )

    new_count = 0
    page = 1
    while True:
        items = adapter.fetch_orders(
            start_time=start_time, end_time=end_time,
            page=page, page_size=50,
        )
        if not items:
            break
        for item in items:
            oid = item["order_id"]
            if oid in existing:
                continue
            existing.add(oid)
            try:
                db.execute(
                    """INSERT INTO orders
                       (order_id, user_id, product_name, product_category,
                        price, purchase_date, purchase_date_days_ago,
                        package_status, activated, status,
                        has_repeat_return_30d, order_already_returned)
                       VALUES (?, 'SYNC', ?, '', ?, date('now'), 0,
                               'unopened', 0, 'delivered', 0, 0)""",
                    (oid, item["product_name"], item["price"]),
                )
                new_count += 1
            except Exception:
                pass
        page += 1
        if len(items) < 50:
            break

    db.commit()
    _log_sync(adapter.platform_name, "orders",
              f"synced: +{new_count} ({start_time} ~ {end_time})")
    return new_count


# ── 日志 ──────────────────────────────────────────────────


def _log_sync(platform: str, data_type: str, message: str) -> None:
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": now_cn().isoformat(),
            "platform": platform,
            "type": data_type,
            "message": message,
        }, ensure_ascii=False) + "\n")

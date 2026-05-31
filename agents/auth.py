"""
用户认证 — JWT + SQLite。支持多坐席登录。

依赖：pip install PyJWT（无额外依赖，Python 标准库 jwt 不完整）
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from typing import Any

from engine.memory_store import get_db


# ── 配置 ──────────────────────────────────────────────────

SECRET_KEY = os.environ.get("JWT_SECRET", "ecommerce-secret-change-me")
TOKEN_EXPIRE_HOURS = 8

# ── 用户表 ────────────────────────────────────────────────


def init_users_table() -> None:
    """建用户表 + 插入默认坐席"""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'agent',
            display_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    db.commit()
    # 默认账号
    _ensure_user(db, "CSR001", "csr001", "坐席小王", "agent")
    _ensure_user(db, "admin", "admin123", "管理员", "admin")


def _ensure_user(db, username: str, password: str, display_name: str,
                 role: str = "agent") -> None:
    row = db.execute("SELECT username FROM users WHERE username=?",
                     (username,)).fetchone()
    if not row:
        h = hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()
        db.execute(
            "INSERT INTO users (username, password_hash, role, display_name) "
            "VALUES (?, ?, ?, ?)", (username, h, role, display_name))
        db.commit()


# ── JWT ───────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    import hmac, json
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    sig = _b64url(hmac.new(SECRET_KEY.encode(),
                 f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def _verify(token: str) -> dict | None:
    import hmac, json
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected = _b64url(hmac.new(
            SECRET_KEY.encode(), f"{header}.{body}".encode(),
            hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(
            __import__("base64").urlsafe_b64decode(body + "==").decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ── 认证接口 ──────────────────────────────────────────────


def login(username: str, password: str) -> dict:
    """登录 → 返回 token + 用户信息"""
    db = get_db()
    row = db.execute(
        "SELECT username, password_hash, role, display_name FROM users "
        "WHERE username=?", (username,)).fetchone()
    if not row:
        return {"error": "用户名不存在"}

    h = hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()
    if h != row["password_hash"]:
        return {"error": "密码错误"}

    payload = {
        "sub": row["username"],
        "role": row["role"],
        "name": row["display_name"],
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_EXPIRE_HOURS * 3600,
    }
    return {
        "token": _sign(payload),
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"],
    }


def verify_token(token: str) -> dict | None:
    """验证 token → 返回用户信息 或 None"""
    payload = _verify(token)
    if not payload:
        return None
    return {
        "username": payload.get("sub", ""),
        "role": payload.get("role", "agent"),
        "display_name": payload.get("name", ""),
    }


def get_operator_id(request_headers: dict) -> str:
    """从请求头提取操作员 ID（用于审计追踪）"""
    auth = request_headers.get("authorization", "")
    if auth.startswith("Bearer "):
        user = verify_token(auth[7:])
        if user:
            return user["username"]
    return "anonymous"


def require_auth(request_headers: dict) -> dict:
    """鉴权装饰器逻辑：返回 user 或 raise 401"""
    auth = request_headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return {"error": "未登录", "code": 401}
    user = verify_token(auth[7:])
    if not user:
        return {"error": "登录已过期", "code": 401}
    return user

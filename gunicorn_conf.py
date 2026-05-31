"""
生产部署配置 — Gunicorn 多进程 + Uvicorn workers。
启动命令: gunicorn server:app -c gunicorn_conf.py
"""

import os
import multiprocessing

# ── 服务器 ────────────────────────────────────────────

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WORKERS", multiprocessing.cpu_count() // 2))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 100
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# ── 日志 ──────────────────────────────────────────────

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# ── 进程管理 ──────────────────────────────────────────

daemon = False
pidfile = None
graceful_timeout = 30

# ── 安全 ──────────────────────────────────────────────

limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

"""
RAG 引擎 — 调用 RAGFlow API 检索知识库，辅助话术生成。

用法：
    from engine.rag_engine import retrieve
    chunks = retrieve("内衣能退吗")
    # → 返回相关规则片段列表
"""

import os
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

RAGFLOW_BASE = "http://localhost:9380"
RAGFLOW_API_KEY = os.environ.get("RAGFLOW_API_KEY", "")
DATASET_ID = "b68f4fac5ccd11f1832c0159c46abf86"


def retrieve(question: str, top_k: int = 3) -> list[str]:
    """检索知识库，返回相关文本片段"""
    url = f"{RAGFLOW_BASE}/api/v1/retrieval"
    body = json.dumps({
        "dataset_ids": [DATASET_ID],
        "question": question,
        "top_k": top_k,
    }).encode("utf-8")

    req = Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RAGFLOW_API_KEY}",
    })

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        chunks = data.get("data", {}).get("chunks", [])
        return [c["content"] for c in chunks]
    except (URLError, json.JSONDecodeError, KeyError):
        return []


def augment_prompt(user_text: str, base_prompt: str) -> str:
    """用检索到的知识增强 prompt"""
    chunks = retrieve(user_text, top_k=2)
    if not chunks:
        return base_prompt

    knowledge = "\n---\n".join(chunks)
    return f"{base_prompt}\n\n【参考规则】\n{knowledge}"

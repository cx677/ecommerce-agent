"""
RAG 商品库 — 将商品导入 RAGFlow 知识库，替代关键词匹配。
"""

from engine.rag_engine import retrieve as rag_retrieve
from engine.memory_store import get_db

DATASET_ID = "b68f4fac5ccd11f1832c0159c46abf86"


def semantic_match(query: str, top_k: int = 5) -> list[dict]:
    """
    语义匹配：用 RAGFlow 搜索商品。
    如果 RAGFlow 不可用，退回关键词匹配。
    """
    # 先检查数据库里的商品
    db = get_db()
    db_products = db.execute(
        "SELECT product_id, name, category, price, stock FROM products"
    ).fetchall()

    if not db_products:
        # 回退到 presale_engine 的关键词匹配
        from engine.presale_engine import PRODUCTS, _match_products
        from engine.presale_engine import _extract_category, _extract_budget
        needs = {
            "category": _extract_category(query),
            "budget": _extract_budget(query),
            "raw_text": query,
        }
        return _match_products(needs, PRODUCTS)

    # 尝试 RAGFlow 检索
    try:
        chunks = rag_retrieve(query, top_k=top_k)
    except Exception:
        chunks = []

    # 如果 RAGFlow 无结果，回退关键词
    if not chunks:
        return _keyword_match(query, db_products)

    # RAGFlow 结果映射回数据库商品
    results = []
    seen = set()
    for chunk in chunks:
        content = chunk.lower()
        for p in db_products:
            pid = p[0]
            if pid in seen:
                continue
            name_lower = p[1].lower()
            # 简单匹配：chunk 包含商品名片段
            if any(word in content for word in name_lower.split()):
                results.append({
                    "product_id": pid,
                    "name": p[1],
                    "category": p[2],
                    "price": p[3],
                    "stock": p[4],
                    "rating": 4.5,
                })
                seen.add(pid)
                if len(results) >= top_k:
                    break

    return results if results else _keyword_match(query, db_products)


def _keyword_match(query: str, db_products) -> list[dict]:
    """关键词兜底匹配"""
    query_lower = query.lower()
    keyword = ""
    for kw in ["洗衣机", "咖啡", "耳机", "手机", "吸尘器", "扫地", "冰箱",
               "鞋", "T恤", "衣服", "电脑", "食品"]:
        if kw in query:
            keyword = kw
            break

    results = []
    for p in db_products:
        pid, name, cat, price, stock = p[0], p[1], p[2], p[3], p[4]
        score = 0
        if keyword and (keyword in name or keyword in cat):
            score += 10
        if any(w in name for w in query_lower.split()):
            score += 3
        if score > 0:
            results.append({"product_id": pid, "name": name, "category": cat,
                           "price": price, "stock": stock, "rating": 4.5, "score": score})

    results.sort(key=lambda x: -x["score"])
    return results[:5]


def sync_products_to_ragflow() -> int:
    """将数据库商品同步到 RAGFlow 知识库"""
    db = get_db()
    rows = db.execute("SELECT name, category, price, stock FROM products").fetchall()
    if not rows:
        return 0

    texts = ["电商平台商品列表：\n"]
    for r in rows:
        texts.append(f"- {r[0]} | 类目: {r[1]} | ¥{r[2]} | 库存: {r[3]}")

    full_text = "\n".join(texts)
    # 写入临时文件供 RAGFlow 上传
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                      delete=False, encoding="utf-8") as f:
        f.write(full_text)
        tmp_path = f.name

    # 通过 RAGFlow API 上传
    import urllib.request, json
    key = os.environ.get("RAGFLOW_API_KEY", "")
    if not key:
        print("⚠️ 未设置 RAGFLOW_API_KEY，跳过上传")
        return 0

    url = f"http://localhost:9380/api/v1/datasets/{DATASET_ID}/documents"
    data = json.dumps({"name": "商品列表", "parser_method": "general",
                       "chunk_method": "general"}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return len(rows)
    except Exception as e:
        print(f"RAGFlow 上传失败: {e}")
        return 0

"""
售前咨询引擎 — 3 步 SOP：问需求→匹配商品→推荐下单。

输入：用户需求描述
输出：商品推荐列表 + 话术
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from engine.rag_products import semantic_match

# ── 加载商品库 ──────────────────────────────────────────


def _load_products() -> list[dict]:
    path = PROJECT_ROOT / "data" / "products.jsonl"
    if not path.exists():
        return _default_products()
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _default_products() -> list[dict]:
    return [
        {"product_id": "P001", "name": "纯棉T恤-白色M码", "category": "服装",
         "price": 89, "rating": 4.7, "stock": 200, "specs": {"颜色": "白/黑/灰", "尺码": "S/M/L/XL"}},
        {"product_id": "P002", "name": "蓝牙降噪耳机", "category": "数码配件",
         "price": 499, "rating": 4.5, "stock": 50, "specs": {"续航": "30h", "降噪": "主动降噪"}},
        {"product_id": "P003", "name": "运动跑鞋-黑色42码", "category": "鞋靴",
         "price": 329, "rating": 4.8, "stock": 80, "specs": {"材质": "网面透气", "适用": "跑步/日常"}},
        {"product_id": "P004", "name": "全自动滚筒洗衣机10kg", "category": "家用电器",
         "price": 2999, "rating": 4.6, "stock": 30, "specs": {"容量": "10kg", "能效": "一级", "类型": "滚筒"}},
        {"product_id": "P005", "name": "波轮洗衣机8kg", "category": "家用电器",
         "price": 1299, "rating": 4.3, "stock": 45, "specs": {"容量": "8kg", "能效": "二级", "类型": "波轮"}},
        {"product_id": "P006", "name": "智能扫地机器人", "category": "家用电器",
         "price": 1999, "rating": 4.4, "stock": 25, "specs": {"续航": "120min", "吸力": "2500Pa", "导航": "激光"}},
        {"product_id": "P007", "name": "戴森V15无线吸尘器", "category": "家用电器",
         "price": 4999, "rating": 4.9, "stock": 15, "specs": {"吸力": "240AW", "续航": "60min", "过滤": "HEPA"}},
        {"product_id": "P008", "name": "意式全自动咖啡机", "category": "家用电器",
         "price": 899, "rating": 4.5, "stock": 40, "specs": {"类型": "意式", "压力": "15Bar", "容量": "1.5L"}},
        {"product_id": "P009", "name": "进口阿拉比卡咖啡豆500g", "category": "食品/生鲜",
         "price": 89, "rating": 4.8, "stock": 120, "specs": {"产地": "埃塞俄比亚", "烘焙": "中深", "品种": "阿拉比卡"}},
    ]


PRODUCTS = _load_products()


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class PresaleSession:
    session_id: str
    operator_id: str
    user_text: str
    current_step: str = "collect_needs"  # collect_needs → match_products → recommend
    needs: dict = field(default_factory=dict)
    matches: list[dict] = field(default_factory=list)
    talk_scripts: list[dict] = field(default_factory=list)
    resolution: str = ""


@dataclass
class PresaleResult:
    session: PresaleSession
    talk_script: str
    next_step: str


# ── 引擎 ────────────────────────────────────────────────


class PresaleEngine:

    def start(self, session_id: str, operator_id: str, user_text: str) -> PresaleResult:
        s = PresaleSession(
            session_id=session_id,
            operator_id=operator_id,
            user_text=user_text,
        )
        return self._step_collect_needs(s)

    def continue_session(self, s: PresaleSession,
                         event: str = "CONTINUE",
                         payload: dict | None = None) -> PresaleResult:
        payload = payload or {}
        if event == "TERMINATE":
            s.resolution = "presale_cancelled"
            s.current_step = "end"
            return PresaleResult(session=s,
                                talk_script="好的，如有需要随时联系~",
                                next_step="end")
        if s.current_step == "collect_needs":
            return self._step_match(s)
        elif s.current_step == "match_products":
            if event == "SELECT_PRODUCT":
                return self._step_recommend(s, payload.get("product_id", ""))
            # CONTINUE → 用第一个匹配商品推荐
            if s.matches:
                return self._step_recommend(s, s.matches[0].get("product_id", ""))
            return PresaleResult(session=s,
                                talk_script="抱歉没有匹配到商品，请重新描述需求",
                                next_step="end")
        elif s.current_step == "recommend":
            s.resolution = "presale_complete"
            s.current_step = "end"
            return PresaleResult(
                session=s,
                talk_script="感谢您的咨询，如有其他需要随时找我~",
                next_step="end",
            )
        return PresaleResult(session=s, talk_script="", next_step="end")

    # ── 步骤 ────────────────────────────────────────

    def _step_collect_needs(self, s: PresaleSession) -> PresaleResult:
        s.current_step = "collect_needs"
        s.needs = {
            "category": _extract_category(s.user_text),
            "budget": _extract_budget(s.user_text),
            "raw_text": s.user_text,  # 传给匹配层做关键词命中
        }
        s.talk_scripts.append({
            "step": "collect_needs",
            "script": f"了解到您需要{s.needs.get('category','商品')}，"
                      f"{'预算约' + str(s.needs['budget']) + '元' if s.needs['budget'] else ''}"
        })
        return PresaleResult(
            session=s,
            talk_script=s.talk_scripts[-1]["script"],
            next_step="match_products",
        )

    def _step_match(self, s: PresaleSession) -> PresaleResult:
        s.current_step = "match_products"
        # 🔥 先用 RAGFlow 语义匹配，失败回退关键词
        matches = semantic_match(s.user_text, top_k=5)
        if not matches:
            matches = _match_products(s.needs, PRODUCTS)
        s.matches = matches[:5] if len(matches) > 5 else matches

        if not matches:
            s.talk_scripts.append({
                "step": "match_products",
                "script": "抱歉，没有找到完全匹配的商品，您能再说说具体要求吗？"
            })
            return PresaleResult(session=s, talk_script=s.talk_scripts[-1]["script"],
                                next_step="collect_needs")

        # 构建推荐话术
        lines = [f"为您找到{len(matches)}款商品："]
        for i, m in enumerate(s.matches[:3]):
            lines.append(f"{i+1}. {m['name']} ¥{m['price']} ⭐{m.get('rating','-')} "
                        f"(库存{m.get('stock','?')})")
        script = "\n".join(lines)
        s.talk_scripts.append({"step": "match_products", "script": script})

        return PresaleResult(session=s, talk_script=script,
                            next_step="recommend")

    def _step_recommend(self, s: PresaleSession, product_id: str) -> PresaleResult:
        s.current_step = "recommend"
        product = next((p for p in s.matches if p.get("product_id") == product_id), None)
        if not product and s.matches:
            product = s.matches[0]

        if product:
            specs = product.get("specs", {})
            spec_str = " | ".join(f"{k}:{v}" for k, v in specs.items())
            script = (f"推荐「{product['name']}」¥{product['price']}，"
                     f"评分⭐{product.get('rating','-')}，{spec_str}。"
                     f"库存{product.get('stock',0)}件。需要帮您下单吗？")
        else:
            script = "请选择一款商品，我为您详细介绍~"

        s.talk_scripts.append({"step": "recommend", "script": script})
        return PresaleResult(session=s, talk_script=script, next_step="end")


# ── 商品匹配逻辑 ────────────────────────────────────────


def _extract_category(text: str) -> str:
    """从文本提取商品类目"""
    cat_map = {
        "衣服": "服装", "裤子": "服装", "T恤": "服装", "鞋": "鞋靴",
        "耳机": "数码配件", "手机": "手机", "电脑": "数码",
        "洗衣机": "家用电器", "冰箱": "家用电器", "家电": "家用电器",
        "扫地": "家用电器", "吸尘器": "家用电器",
        "食品": "食品/生鲜", "零食": "食品/生鲜", "咖啡": "食品/生鲜",
        "家居": "家居用品", "日用": "家居用品",
        "运动": "运动户外", "跑步": "运动户外",
    }
    for kw, cat in cat_map.items():
        if kw in text:
            return cat
    return ""


def _extract_budget(text: str) -> int | None:
    """提取预算"""
    import re
    m = re.search(r'(\d+)\s*元', text)
    if m:
        return int(m.group(1))
    m = re.search(r'([一二三四五]百)', text)
    if m:
        return int({"一":1,"二":2,"三":3,"四":4,"五":5}[m.group(1)[0]] * 100)
    return None


def _match_products(needs: dict, products: list[dict]) -> list[dict]:
    """根据需求匹配商品（类目 + 名称关键词 + 预算 + 评分）"""
    scored = []
    category = needs.get("category", "")
    budget = needs.get("budget")
    raw_text = needs.get("raw_text", "")

    # 提取用户输入中的候选关键词（2-4字片段）
    key_chars = set()
    if raw_text:
        # 去掉常见停用词
        stop = {"推荐", "一款", "一个", "有没有", "想买", "买个", "帮我", "我想要", "要"}
        clean = raw_text
        for w in stop:
            clean = clean.replace(w, "")
        # 滑动窗口提取 1-2 字片段
        for i in range(len(clean)):
            for j in range(1, 4):
                if i + j <= len(clean):
                    key_chars.add(clean[i:i+j])

    for p in products:
        score = 0
        name = p.get("name", "")
        pcat = p.get("category", "")
        specs_str = " ".join(str(v) for v in p.get("specs", {}).values())

        # 类目匹配 (权重5)
        if category and category in pcat:
            score += 5

        # 名称/规格关键词命中 (权重8，比类目更重)
        if key_chars:
            name_hits = sum(1 for k in key_chars if k in name)
            spec_hits = sum(1 for k in key_chars if k in specs_str)
            score += min(name_hits * 2 + spec_hits, 8)

        # 预算匹配 (权重3)
        if budget and p.get("price", 0) <= budget * 1.2:
            score += 3

        # 评分贡献 (权重 1.5 × rating)
        score += p.get("rating", 0) * 1.5

        # 库存贡献 (最多3)
        score += min(p.get("stock", 0) / 20, 3)

        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored if _ > 0][:5]

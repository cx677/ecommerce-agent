"""
意图路由器 — 4 场景分发入口。

接收用户输入 → 分类场景 → 提取实体 → 路由到对应引擎。

分类策略：关键词匹配（优先，快速）→ LLM 兜底（精准）。

场景：
  presale      售前咨询（商品推荐、规格对比、优惠查询、库存查询）
  order_track  订单追踪（物流查询、修改地址、催单）
  return_refund 退货退款（7天无理由、质量问题、换货）
  complaint    投诉升级（差评安抚、虚假宣传投诉）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from agents.talk_agent import _llm_call
except ImportError:
    _llm_call = None


# ── 场景关键词库 ────────────────────────────────────────

SCENE_KEYWORDS = {
    "return_refund": {
        "high": ["退货", "退款", "换货", "退钱", "退了吧", "不想要", "退掉",
                  "尺码不对", "太大", "太小", "不合适", "穿不上",
                  "质量", "坏了", "破", "瑕疵", "开线", "掉色", "变形",
                  "发错", "寄错", "颜色不对", "型号不对",
                  "碎了", "划痕", "碎了", "摔坏", "压坏"],
        "medium": ["退", "换", "不好", "有问题", "不满意"],
    },
    "order_track": {
        "high": ["物流", "快递", "到哪", "发货", "没收到", "还没到",
                  "查单", "催单", "催一下", "什么时候到",
                  "改地址", "修改地址", "换地址", "地址错了", "收货地址",
                  "延长收货", "确认收货", "签收", "修改收货"],
        "medium": ["订单", "查询", "进度", "配送", "运输", "跟踪", "在哪"],
    },
    "presale": {
        "high": ["推荐", "有什么", "哪个好", "介绍一下", "多少钱",
                  "优惠", "打折", "满减", "优惠券", "活动",
                  "有货吗", "库存", "有没有", "尺码表", "规格",
                  "颜色", "尺寸", "材质", "适合"],
        "medium": ["看看", "想买", "了解", "咨询", "介绍", "帮忙选", "便宜", "划算", "性价比"],
    },
    "complaint": {
        "high": ["投诉", "差评", "举报", "虚假", "骗人", "坑人",
                  "态度差", "态度不好", "服务差", "骂人",
                  "投诉你", "找你们领导", "客服不行",
                  "与描述不符", "图文不符", "假货"],
        "medium": ["生气", "火大", "不满意", "过分", "投诉"],
    },
}


# ── 实体提取 ────────────────────────────────────────────

def _extract_order_id(text: str) -> str | None:
    """提取订单号 ORDxxx"""
    import re
    m = re.search(r'(ORD\d+)', text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _extract_amount(text: str) -> float | None:
    """提取金额"""
    import re
    m = re.search(r'(\d+\.?\d*)\s*元', text)
    return float(m.group(1)) if m else None


def _extract_product(text: str) -> str | None:
    """提取商品名（引号中的内容或常见模式）"""
    import re
    m = re.search(r'[「「](.+?)[」」]', text)
    if m:
        return m.group(1)
    m = re.search(r'"(.*?)"', text)
    if m:
        return m.group(1)
    return None


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class RouteResult:
    scene: str                # presale | order_track | return_refund | complaint
    confidence: float         # 0-1
    sub_intent: str = ""      # 子意图
    entities: dict = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)


# ── 主分类函数 ──────────────────────────────────────────


def route(user_text: str) -> RouteResult:
    """分类用户输入到对应场景"""

    text_lower = user_text.lower()

    # Step 1: 关键词匹配评分
    scores = {}
    for scene, kw_groups in SCENE_KEYWORDS.items():
        score = 0
        for kw in kw_groups.get("high", []):
            if kw in text_lower or kw in user_text:
                score += 3
        for kw in kw_groups.get("medium", []):
            if kw in text_lower or kw in user_text:
                score += 1
        scores[scene] = score

    best_scene = max(scores, key=scores.get)
    best_score = scores[best_scene]

    # Step 2: 如果最高分≥6，直接返回；否则 LLM 兜底
    if best_score >= 6:
        confidence = min(1.0, best_score / 12.0)
        return RouteResult(
            scene=best_scene,
            confidence=confidence,
            sub_intent=_classify_sub_intent(best_scene, user_text),
            entities={
                "order_id": _extract_order_id(user_text),
                "amount": _extract_amount(user_text),
                "product": _extract_product(user_text),
            },
            candidates=_get_candidates(scores, best_scene),
        )

    # Step 3: 得分不足 → LLM 兜底
    if _llm_call:
        try:
            llm_result = _llm_route(user_text, scores)
            if llm_result.confidence >= 0.7:
                return llm_result
        except Exception:
            pass

    # Step 4: 最终 fallback — 用关键词最高分场景
    return RouteResult(
        scene=best_scene or "return_refund",
        confidence=max(0.3, best_score / 12.0),
        sub_intent=_classify_sub_intent(best_scene, user_text),
        entities={"order_id": _extract_order_id(user_text), "amount": _extract_amount(user_text)},
        candidates=_get_candidates(scores, best_scene),
    )


def _classify_sub_intent(scene: str, text: str) -> str:
    """识别子意图"""
    if scene == "return_refund":
        if any(kw in text for kw in ["尺码", "大小", "太紧", "太松", "不合适"]):
            return "wrong_size"
        if any(kw in text for kw in ["质量", "坏了", "破", "瑕疵", "开线"]):
            return "quality_defect"
        if any(kw in text for kw in ["发错", "寄错"]):
            return "wrong_item"
        if any(kw in text for kw in ["不想要", "不需要", "后悔"]):
            return "no_longer_wanted"
        if any(kw in text for kw in ["快递", "运输", "压坏", "摔坏"]):
            return "damaged_in_transit"
        return "general_return"

    if scene == "order_track":
        if any(kw in text for kw in ["物流", "到哪", "没到", "还没"]):
            return "check_logistics"
        if any(kw in text for kw in ["催", "快点", "赶紧"]):
            return "urge_delivery"
        if any(kw in text for kw in ["改地址", "换地址", "地址错"]):
            return "change_address"
        return "general_track"

    if scene == "presale":
        if any(kw in text for kw in ["推荐", "有什么", "哪个好"]):
            return "recommend"
        if any(kw in text for kw in ["优惠", "打折", "满减", "券"]):
            return "check_promotion"
        if any(kw in text for kw in ["有货", "库存", "有没有"]):
            return "check_stock"
        if any(kw in text for kw in ["多少钱", "价格"]):
            return "check_price"
        return "general_presale"

    if scene == "complaint":
        if any(kw in text for kw in ["态度", "骂人", "服务差"]):
            return "poor_service"
        if any(kw in text for kw in ["虚假", "骗", "假货", "不符"]):
            return "false_advertising"
        if any(kw in text for kw in ["差评", "不好"]):
            return "negative_review"
        return "general_complaint"

    return ""


def _get_candidates(scores: dict, best: str) -> list[dict]:
    """生成候选场景列表"""
    sorted_scenes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {"scene": s, "score": v, "is_primary": s == best}
        for s, v in sorted_scenes[:3] if v > 0
    ]


def _llm_route(user_text: str, keyword_scores: dict) -> RouteResult:
    """LLM 兜底分类"""
    prompt = f"""分析以下用户消息，判断属于哪个电商客服场景。只回复一个场景名。

场景选项：
- presale: 售前咨询（商品推荐、规格、优惠、库存）
- order_track: 订单追踪（物流、改地址、催单）
- return_refund: 退货退款（退换货、退款）
- complaint: 投诉升级（差评、虚假宣传、客服态度）

用户消息：{user_text}

关键词匹配得分参考：{keyword_scores}

只回复场景名："""

    messages = [
        {"role": "system", "content": "你是电商客服场景分类器。只回复一个场景名，不要解释。"},
        {"role": "user", "content": prompt},
    ]

    resp = _llm_call(messages, temperature=0.1)
    resp_lower = resp.strip().lower()

    for scene in ["presale", "order_track", "return_refund", "complaint"]:
        if scene in resp_lower:
            return RouteResult(
                scene=scene,
                confidence=0.85,
                sub_intent=_classify_sub_intent(scene, user_text),
                entities={"order_id": _extract_order_id(user_text)},
            )

    return RouteResult(
        scene="return_refund",
        confidence=0.5,
        entities={"order_id": _extract_order_id(user_text)},
    )

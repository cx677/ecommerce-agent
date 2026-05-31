"""
LLM 话术生成器 — 调用火山引擎 doubao 生成口语化客服回复。

输入：会话上下文（订单、意图、资格结果、退款方案）
输出：自然语言话术
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from engine.rag_engine import retrieve as rag_retrieve

# ── 配置 ──────────────────────────────────────────────
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
API_KEY = os.environ.get("ARK_API_KEY") or os.environ.get("VOLCENGINE_API_KEY", "")
MODEL = "doubao-seed-2-0-pro-260215"


def _llm_call(messages: list[dict], temperature: float = 0.5,
              max_retries: int = 2) -> str:
    """调用火山引擎 Coding Plan API，带指数退避重试"""
    if not API_KEY:
        return ""

    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(verify=False, timeout=30) as client:
                resp = client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": MODEL,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": 512,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)[:100]

        if attempt < max_retries:
            import time
            time.sleep(1.5 ** attempt)  # 指数退避: 0, 1.5, 2.25s

    return ""


# ── Prompt 模板 ───────────────────────────────────────

SYSTEM_PROMPT = """你是一个电商平台的客服坐席助手，负责为坐席生成专业的客户回复话术。

要求：
- 口语化，像真人客服说话，不要书面语
- 使用"亲"、"您"等亲切称谓
- 关键信息（金额、时间、退货单号）必须加粗重点强调
- 语气根据场景调整：退款→温暖，拒绝→委婉但坚定，升级→安抚
- 每次回复控制在 80 字以内，简洁清晰
- 永远不要承诺任何超出系统判定结果的内容
- 禁止说"我帮您申请"而要直接给确定答复
"""


def generate_talk(session_data: dict) -> str:
    """
    根据会话状态生成客服话术。

    Args:
        session_data: 包含 current_node, order, intent, eligibility, refund 等字段

    Returns:
        自然语言话术字符串
    """
    node = session_data.get("current_node", "")
    order = session_data.get("order", {})
    intent = session_data.get("intent", {})
    eligibility = session_data.get("eligibility", {})
    refund = session_data.get("refund", {})

    # 构建上下文
    ctx_parts = []

    # ── RAG 检索增强：从知识库捞出相关规则 ──
    try:
        user_text = session_data.get("user_text", "")
        if user_text:
            rag_chunks = rag_retrieve(user_text, top_k=2)
            if rag_chunks:
                rag_text = "\n".join(c[:300] for c in rag_chunks)  # 截断防止过长
                ctx_parts.append(f"【平台退货规则（参考）】\n{rag_text}")
    except Exception:
        pass  # RAG 不可用时静默降级

    if order:
        ctx_parts.append(
            f"订单信息：{order.get('product_name','')}，"
            f"金额¥{order.get('price',0)}，"
            f"购买于{order.get('purchase_date_days_ago',0)}天前"
        )

    if intent:
        ctx_parts.append(f"用户退货原因：{intent.get('intent_name','')}")

    if eligibility and eligibility.get("checks"):
        checks_str = "；".join(
            f"{'✓' if c.get('passed') else '✗'} {c.get('rule_id','')}: {c.get('reason','')}"
            for c in eligibility["checks"]
        )
        ctx_parts.append(f"资格判定结果（{'通过' if eligibility.get('eligible') else '未通过'}）：{checks_str}")

    if refund:
        ctx_parts.append(
            f"退款方案：{refund.get('action','')}，"
            f"退款金额¥{refund.get('refund_amount',0)}，"
            f"运费{refund.get('shipping','未指定')}，"
            f"备注：{refund.get('notes','')}"
        )

    if session_data.get("resolution"):
        ctx_parts.append(f"最终结果：{session_data.get('resolution')}")

    context = "\n".join(ctx_parts)

    # 选择 Prompt
    user_prompt = _prompt_for_node(node, context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = _llm_call(messages)
    return result if result else _fallback_talk(session_data)


def _prompt_for_node(node: str, context: str) -> str:
    """根据当前节点选择合适的 prompt"""

    prompts = {
        "query_order": f"""用户来电咨询退货。请生成第一步话术：告知用户已查到他的订单，并简要确认订单信息。

{context}

请生成坐席话术：""",

        "classify_intent": f"""用户说明了退货原因。请生成确认意图的话术：用一句话复述用户的退货原因，询问是否正确。

{context}

请生成坐席话术：""",

        "check_eligibility": f"""系统已完成退货资格判定。请根据判定结果生成话术：
- 如果资格通过：告知用户符合退货条件，正在计算退款方案
- 如果资格未通过：礼貌地说明不通过的原因，如是升级则安抚情绪
- 如果是黑名单拒退：委婉解释为什么这类商品不能退

{context}

请生成坐席话术：""",

        "calculate_refund": f"""退款方案已计算完毕。请生成话术告知用户具体方案：
- 说清退款金额、运费谁承担
- 如果是质量问题→表达歉意，强调全额退款+免运费
- 如果是折旧→耐心解释折旧原因
- 如果是换货→说明换货流程

{context}

请生成坐席话术：""",

        "execute_return": f"""坐席即将执行退货操作。请生成确认话术：
- 请用户确认退货操作
- 提醒退款到账时间（3-5个工作日）
- 提醒用户保留退货凭证

{context}

请生成坐席话术：""",

        "end": f"""退货会话即将结束。请生成结束话术：
- 告知最终结果
- 如果是退款→提醒退款到账时间（3-5个工作日）
- 如果是换货→说明换货流程和时间
- 如果是拒绝→用【平台退货规则】里的原文解释原因，语气委婉但坚定
- 如果是升级→安抚情绪，告知后续会有人工联系
- 结尾表达感谢

{context}

请生成坐席话术：""",
    }

    return prompts.get(node, f"请根据以下上下文生成合适的客服话术：\n\n{context}\n\n请生成坐席话术：")


# ── 降级话术（LLM 不可用时）───────────────────────────

def _fallback_talk(data: dict) -> str:
    """硬编码降级话术"""
    node = data.get("current_node", "")
    order = data.get("order", {})
    intent = data.get("intent", {})
    eligibility = data.get("eligibility", {})
    refund = data.get("refund", {})
    resolution = data.get("resolution", "")

    product = order.get("product_name", "商品")
    price = order.get("price", 0)
    days = order.get("purchase_date_days_ago", 0)

    if node == "query_order":
        return f"亲，已查到您的订单「{product}」，金额¥{price}，{days}天前购买的。请问您是因为什么原因想退货呢？"

    if node == "classify_intent":
        reason = intent.get("intent_name", "其他原因")
        return f"了解到您是因为「{reason}」申请退货，对吗？"

    if node == "check_eligibility":
        if eligibility.get("eligible"):
            return "亲，经核查您的订单符合退货条件，正在为您计算退款方案~"
        if eligibility.get("escalate"):
            reasons = eligibility.get("failed_reasons", ["需要人工审核"])
            return f"亲，{'；'.join(reasons)}。已为您转接高级客服人工处理，请稍候~"
        reasons = eligibility.get("failed_reasons", ["不符合退货条件"])
        return f"亲，非常抱歉，{'；'.join(reasons)}。如有疑问可咨询高级客服。"

    if node == "calculate_refund":
        amount = refund.get("refund_amount", 0)
        shipping = refund.get("shipping", "")
        notes = refund.get("notes", "")
        return f"亲，退款方案如下：退款金额¥{amount}，{shipping}。{notes}"

    if node == "execute_return":
        amount = refund.get("refund_amount", 0)
        return f"亲，确认为您办理退货，退款金额¥{amount}，将在3-5个工作日退回您的原支付方式。请点击确认按钮办理。"

    if node == "end":
        if resolution:
            return f"{resolution}。感谢您的耐心，祝您生活愉快！"
        if refund.get("approved"):
            return f"亲，退货已办理成功！退款¥{refund.get('refund_amount',0)}将在3-5个工作日到账。感谢您的耐心！"
        return "感谢您的来电，如有其他问题随时联系我们。祝您生活愉快！"

    return "亲，正在为您处理中，请稍候~"

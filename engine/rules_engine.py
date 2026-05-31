"""
规则引擎 — 15 条退货退款规则执行器。

三类规则：
  A. 资格判定 (6 条 AND) — 任一失败 → 拒绝或升级
  B. 退款计算 (4 档) — 根据条件×金额
  C. 禁退黑名单 (5 类 OR) — 命中任一 → 拒绝

纯函数，无副作用。所有阈值从 rules.yaml + config.yaml 加载。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


# ── 配置加载 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    if not path.exists() or yaml is None:
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> dict:
    """合并 config.yaml + rules/return_rules.yaml"""
    config = _load_yaml(PROJECT_ROOT / "config.yaml")
    rules = _load_yaml(PROJECT_ROOT / "rules" / "return_rules.yaml")
    return {**config, "rules": rules}


CONFIG = load_config()
RULES = CONFIG.get("rules", {})
THRESHOLDS = CONFIG.get("thresholds", {})


def _t(key: str, default: Any = 0) -> Any:
    """读取阈值：优先 config.yaml，fallback rules.yaml"""
    return THRESHOLDS.get(key, default)


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class RuleCheck:
    rule_id: str
    passed: bool
    reason: str = ""
    actual: Any = None
    expected: Any = None


@dataclass
class EligibilityResult:
    eligible: bool
    checks: list[RuleCheck] = field(default_factory=list)
    escalate: bool = False
    depreciation: bool = False

    def failed_reasons(self) -> list[str]:
        return [c.reason for c in self.checks if not c.passed]


@dataclass
class RefundResult:
    approved: bool
    refund_amount: float = 0.0
    action: str = ""           # full_refund / partial_refund / exchange / reject / escalate
    shipping: str = ""         # "平台承担" / "用户承担"
    notes: str = ""
    reason: str = ""


@dataclass
class IntentResult:
    intent_id: str
    intent_name: str
    confidence: float = 1.0
    default_action: str = ""


@dataclass
class BlacklistResult:
    blocked: bool
    blocker_id: str = ""
    reason: str = ""


# ── A. 资格判定 ───────────────────────────────────────────


def check_eligibility(order: dict, return_request: dict) -> EligibilityResult:
    """6 条 AND 规则：全部通过才能进入退款流程"""

    checks: list[RuleCheck] = []
    escalate = False
    depreciation = False

    # R_ELIG_01: 退货窗口
    days_ago = order.get("purchase_date_days_ago", 0)
    window = _t("return_window_days", 7)
    grace = _t("grace_period_days", 7)

    if days_ago <= window:
        checks.append(RuleCheck("R_ELIG_01_WINDOW", True, actual=days_ago, expected=f"<= {window}"))
    elif days_ago <= window + grace:
        checks.append(RuleCheck("R_ELIG_01_WINDOW", False,
            reason=f"您的订单已购买{days_ago}天，超过{window}天无理由退货期，需人工审核",
            actual=days_ago, expected=f"<= {window}"))
        escalate = True
    else:
        checks.append(RuleCheck("R_ELIG_01_WINDOW", False,
            reason=f"您的订单已购买{days_ago}天，超过{window+grace}天不支持退货",
            actual=days_ago, expected=f"<= {window+grace}"))

    # R_ELIG_02: 禁退黑名单
    bl = check_blacklist(order)
    if bl.blocked:
        checks.append(RuleCheck("R_ELIG_02_BLACKLIST", False,
            reason=bl.reason, actual=order.get("product_category"),
            expected="非禁退类目"))
    else:
        checks.append(RuleCheck("R_ELIG_02_BLACKLIST", True,
            actual=order.get("product_category")))

    # R_ELIG_03: 商品状态
    pkg_status = return_request.get("package_status", order.get("package_status", "unopened"))
    if pkg_status == "unopened":
        checks.append(RuleCheck("R_ELIG_03_RESALABLE", True, actual="unopened"))
    elif pkg_status == "opened_intact":
        checks.append(RuleCheck("R_ELIG_03_RESALABLE", True,
            actual="opened_intact"))
        depreciation = True
    elif pkg_status == "damaged":
        checks.append(RuleCheck("R_ELIG_03_RESALABLE", False,
            reason="商品已损坏，不符合退货条件",
            actual="damaged", expected="intact"))

    # R_ELIG_04: 30天内重复退货
    has_repeat = order.get("has_repeat_return_30d", False)
    checks.append(RuleCheck("R_ELIG_04_NO_REPEAT",
        not has_repeat,
        reason="该商品在30天内已申请过退货" if has_repeat else "",
        actual=has_repeat, expected=False))

    # R_ELIG_05: 同一订单未退过（幂等）
    already = order.get("order_already_returned", False)
    checks.append(RuleCheck("R_ELIG_05_NO_DUPLICATE",
        not already,
        reason="该订单已完成退货，无法重复申请" if already else "",
        actual=already, expected=False))

    # R_ELIG_06: 金额上限
    amount = order.get("price", 0)
    limit = _t("max_refund_amount", 5000)
    if amount <= limit:
        checks.append(RuleCheck("R_ELIG_06_AMOUNT", True,
            actual=amount, expected=f"<= {limit}"))
    else:
        checks.append(RuleCheck("R_ELIG_06_AMOUNT", False,
            reason=f"订单金额{amount}元超过{limit}元在线处理上限",
            actual=amount, expected=f"<= {limit}"))
        escalate = True

    # 汇总
    all_passed = all(c.passed for c in checks)

    return EligibilityResult(
        eligible=all_passed and not escalate,
        checks=checks,
        escalate=escalate,  # 只有窗口期/金额超限才升级，黑名单/重复是直接拒绝
        depreciation=depreciation,
    )


# ── B. 退款计算 ───────────────────────────────────────────


def calculate_refund(order: dict, intent: IntentResult,
                     eligibility: EligibilityResult) -> RefundResult:
    """根据退货原因 + 资格结果计算退款方案"""

    # 资格不通过
    if not eligibility.eligible:
        if eligibility.escalate:
            return RefundResult(
                approved=False, action="escalate",
                notes="已为您转接高级客服人工处理", reason="资格未通过")
        return RefundResult(
            approved=False, action="reject",
            notes="\n".join(eligibility.failed_reasons()),
            reason="资格未通过")

    amount = order.get("price", 0)
    shipping = _t("shipping_fee", 12)
    dep_rate = _t("depreciation_rate", 0.10)
    intent_id = intent.intent_id

    # 质量问题 (INTENT_02 / INTENT_05) → 全额+补偿运费
    if intent_id in ("INTENT_02_QUALITY_DEFECT", "INTENT_03_WRONG_ITEM", "INTENT_05_DAMAGED_IN_TRANSIT"):
        return RefundResult(
            approved=True, refund_amount=amount, action="full_refund",
            shipping="平台承担+补偿运费", notes=f"质量问题全额退款，额外补偿{shipping}元运费",
            reason="quality")

    # 尺码不对 → 换货或退款-运费
    if intent_id == "INTENT_01_WRONG_SIZE":
        if eligibility.depreciation:
            return RefundResult(
                approved=True,
                refund_amount=round(amount * (1 - dep_rate), 2),
                action="partial_refund",
                shipping="用户承担",
                notes=f"已拆封扣除{int(dep_rate*100)}%折旧费，退款{round(amount * (1 - dep_rate), 2)}元。也可选择免费换货",
                reason="wrong_size_opened")
        return RefundResult(
            approved=True, refund_amount=amount - shipping, action="refund_minus_shipping",
            shipping="用户承担",
            notes=f"退款{amount - shipping}元（已扣除{shipping}元运费）。也可选择免费换货",
            reason="wrong_size")

    # 不想要了 → 全额退款（用户付运费）
    if intent_id == "INTENT_04_NO_LONGER_WANTED":
        if eligibility.depreciation:
            return RefundResult(
                approved=True,
                refund_amount=round(amount * (1 - dep_rate), 2),
                action="partial_refund",
                shipping="用户承担",
                notes=f"已拆封扣除{int(dep_rate*100)}%折旧费，退款{round(amount * (1 - dep_rate), 2)}元",
                reason="no_longer")
        return RefundResult(
            approved=True, refund_amount=amount, action="full_refund",
            shipping="用户承担",
            notes=f"全额退款{amount}元，退货运费由您承担",
            reason="no_longer")

    # fallback
    return RefundResult(
        approved=True, refund_amount=amount, action="full_refund",
        shipping="用户承担", notes=f"退款{amount}元", reason="general")


# ── C. 禁退黑名单 ─────────────────────────────────────────


def check_blacklist(order: dict) -> BlacklistResult:
    """检查商品是否在5类禁退黑名单中（OR规则）"""
    category = order.get("product_category", "")
    bl_items = RULES.get("blacklist_categories", [])

    for item in bl_items:
        if item["category"] in category or category in item["category"]:
            return BlacklistResult(
                blocked=True,
                blocker_id=item.get("id", ""),
                reason=item.get("reason", ""),
            )

    # 已激活电子产品特殊检查
    if "手机" in category or "电脑" in category or "平板" in category:
        if order.get("activated", False):
            return BlacklistResult(
                blocked=True,
                blocker_id="BL_05_ACTIVATED_ELECTRONICS",
                reason="已激活的电子产品不支持退货",
            )

    return BlacklistResult(blocked=False)


# ── D. 意图分类 ───────────────────────────────────────────


def classify_intent(user_text: str) -> IntentResult:
    """关键词匹配识别退货意图（预留 LLM 分类器接口）"""
    user_text_lower = user_text.lower()
    intents = RULES.get("intent_categories", {})

    for intent_id, cfg in intents.items():
        keywords = cfg.get("keywords", [])
        for kw in keywords:
            if kw in user_text_lower or kw in user_text:
                return IntentResult(
                    intent_id=intent_id,
                    intent_name=cfg.get("name", ""),
                    confidence=0.9,
                    default_action=cfg.get("default_action", ""),
                )

    # fallback: 不想要了
    return IntentResult(
        intent_id="INTENT_04_NO_LONGER_WANTED",
        intent_name="不想要了",
        confidence=0.5,
        default_action="no_longer_wanted",
    )

"""
ABL 溯源学习 — 规则判"不退"但用户情绪激烈时，记录冲突到 JSONL。

核心逻辑：
  规则引擎给出 reject / escalate 结果
  → 检测用户文本是否包含强烈情绪关键词
  → 如果情绪激烈但规则说"不" → 记录冲突，等待人工裁决

人工裁决后 → 反馈到规则引擎（修改阈值/增加例外/标注训练样本）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 路径配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / ".state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
CONFLICTS_FILE = STATE_DIR / "conflicts.jsonl"


def now_cn() -> datetime:
    return datetime.now()


# ── 情绪关键词 ────────────────────────────────────────────
# 四级强度：轻 → 中 → 强 → 极强
EMOTION_KEYWORDS = {
    "mild": [
        "不太满意", "有点失望", "不太合适", "能通融吗",
        "能不能特殊处理", "帮帮忙",
    ],
    "moderate": [
        "不满意", "很失望", "麻烦", "尽快处理", "不行我就投诉",
        "你们这样不对", "这说不过去", "不合理",
    ],
    "strong": [
        "很生气", "太生气", "太过分", "太差劲", "太烂",
        "凭什么", "不公平", "坑人", "骗人", "我要投诉",
        "不能接受", "无法接受", "太让人失望", "你再说一遍",
        "你们必须", "我不管", "必须退", "不退不行",
    ],
    "extreme": [
        "欺诈", "黑心", "无良", "店大欺客", "欺人太甚",
        "不退了直接投诉", "315", "消协", "维权",
        "报警", "曝光你们", "拉横幅", "网上曝光",
    ],
}

# 进入冲突记录的强度阈值：moderate 及以上
RECORD_THRESHOLD = "moderate"


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class EmotionResult:
    has_emotion: bool
    level: str = ""  # none / mild / moderate / strong / extreme
    matched_keywords: list[str] = field(default_factory=list)
    score: int = 0  # mild=1, moderate=2, strong=3, extreme=4


@dataclass
class ConflictRecord:
    conflict_id: str
    session_id: str
    operator_id: str
    recorded_at: str

    # 订单上下文
    order_id: str = ""
    product_name: str = ""
    product_category: str = ""
    price: float = 0.0

    # 用户输入
    user_text: str = ""

    # 规则判定
    rule_results: dict = field(default_factory=dict)

    # 情绪分析
    emotion_level: str = ""
    emotion_keywords: list[str] = field(default_factory=list)
    emotion_score: int = 0

    # 人工裁决
    adjudication: str | None = None  # null=待裁决, uphold=维持原判, override=例外放行, escalate=升级
    adjudicated_by: str = ""
    adjudicated_at: str = ""
    adjudication_notes: str = ""

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "recorded_at": self.recorded_at,
            "order_id": self.order_id,
            "product_name": self.product_name,
            "product_category": self.product_category,
            "price": self.price,
            "user_text": self.user_text,
            "rule_results": self.rule_results,
            "emotion_level": self.emotion_level,
            "emotion_keywords": self.emotion_keywords,
            "emotion_score": self.emotion_score,
            "adjudication": self.adjudication,
            "adjudicated_by": self.adjudicated_by,
            "adjudicated_at": self.adjudicated_at,
            "adjudication_notes": self.adjudication_notes,
        }


# ── 情绪检测 ──────────────────────────────────────────────


def detect_emotion(user_text: str) -> EmotionResult:
    """检测用户文本中的情绪强度"""
    matched = []
    max_level = "none"
    max_score = 0

    level_scores = {"mild": 1, "moderate": 2, "strong": 3, "extreme": 4}

    for level, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in user_text:
                matched.append(kw)
                score = level_scores[level]
                if score > max_score:
                    max_score = score
                    max_level = level

    has_emotion = max_level != "none" and max_score >= level_scores.get(RECORD_THRESHOLD, 2)

    return EmotionResult(
        has_emotion=has_emotion,
        level=max_level,
        matched_keywords=matched,
        score=max_score,
    )


# ── 冲突记录 ──────────────────────────────────────────────


def record_conflict(
    session_id: str,
    operator_id: str,
    order: dict,
    user_text: str,
    eligibility: dict,
    refund: dict | None = None,
) -> ConflictRecord | None:
    """
    检测情绪 → 如果足够激烈 → 记录冲突到 JSONL。
    返回 ConflictRecord 或 None（情绪不够激烈 or 规则已放行）。
    """
    emotion = detect_emotion(user_text)
    if not emotion.has_emotion:
        return None

    # 只有规则说"不"时才记录（eligible=False 且 非escalate）
    is_rejected = (
        not eligibility.get("eligible", True)
        and not eligibility.get("escalate", False)
    )

    # 如果是 escalate 但情绪极强（strong+），也记录
    is_escalate_with_anger = (
        eligibility.get("escalate", False)
        and emotion.score >= 3
    )

    if not is_rejected and not is_escalate_with_anger:
        return None

    record = ConflictRecord(
        conflict_id=str(uuid.uuid4())[:12],
        session_id=session_id,
        operator_id=operator_id,
        recorded_at=now_cn().isoformat(),
        order_id=order.get("order_id", ""),
        product_name=order.get("product_name", ""),
        product_category=order.get("product_category", ""),
        price=order.get("price", 0),
        user_text=user_text,
        rule_results={
            "eligible": eligibility.get("eligible", False),
            "escalate": eligibility.get("escalate", False),
            "failed_reasons": eligibility.get("failed_reasons", []),
            "checks": eligibility.get("checks", []),
        },
        emotion_level=emotion.level,
        emotion_keywords=emotion.matched_keywords,
        emotion_score=emotion.score,
    )

    _append_conflict(record)
    return record


def _append_conflict(record: ConflictRecord) -> None:
    """追加一条冲突记录到 JSONL"""
    with open(CONFLICTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


# ── 查询接口 ──────────────────────────────────────────────


def load_conflicts(status: str = "all") -> list[dict]:
    """
    加载冲突记录。
    status: "all" | "pending" | "resolved" | "uphold" | "override" | "escalate"
    """
    if not CONFLICTS_FILE.exists():
        return []

    decision_filters = {"uphold", "override", "escalate"}

    results = []
    with open(CONFLICTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                adj = rec.get("adjudication")
                if status == "pending" and adj is not None:
                    continue
                if status == "resolved" and adj is None:
                    continue
                if status in decision_filters and adj != status:
                    continue
                results.append(rec)
            except json.JSONDecodeError:
                continue

    return results


def adjudicate(
    conflict_id: str,
    decision: str,  # uphold / override / escalate
    operator_id: str = "",
    notes: str = "",
) -> bool:
    """
    人工裁决一条冲突记录。
    更新 JSONL 中对应行（重写整个文件）。
    """
    if not CONFLICTS_FILE.exists():
        return False

    lines = []
    found = False
    with open(CONFLICTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line_data = line.strip()
            if not line_data:
                lines.append(line)  # keep blank lines
                continue
            try:
                rec = json.loads(line_data)
            except json.JSONDecodeError:
                lines.append(line)
                continue

            if rec.get("conflict_id") == conflict_id:
                rec["adjudication"] = decision
                rec["adjudicated_by"] = operator_id
                rec["adjudicated_at"] = now_cn().isoformat()
                rec["adjudication_notes"] = notes
                found = True
            lines.append(json.dumps(rec, ensure_ascii=False) + "\n")

    if not found:
        return False

    with open(CONFLICTS_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return True


def get_stats() -> dict:
    """获取冲突统计"""
    conflicts = load_conflicts("all")
    pending = [c for c in conflicts if c.get("adjudication") is None]
    resolved = [c for c in conflicts if c.get("adjudication") is not None]

    by_decision = {}
    for c in resolved:
        d = c.get("adjudication", "unknown")
        by_decision[d] = by_decision.get(d, 0) + 1

    by_level = {}
    for c in conflicts:
        lv = c.get("emotion_level", "unknown")
        by_level[lv] = by_level.get(lv, 0) + 1

    return {
        "total": len(conflicts),
        "pending": len(pending),
        "resolved": len(resolved),
        "by_decision": by_decision,
        "by_emotion_level": by_level,
    }

"""
8 条退货 Golden Path + 3 条新场景回归测试。

退货流程：start → query_order → CONTINUE → classify_intent → 
          USER_ACCEPTED → check_eligibility → CONTINUE → 
          calculate_refund → CONTINUE → execute_return → 
          CONFIRM_ACTION → end
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import FullAgent
from engine.memory_store import get_db, init_mock_data


def setup():
    db = get_db()
    init_mock_data(db)
    db.execute("UPDATE orders SET order_already_returned = 0")
    db.execute("DELETE FROM returns")
    db.execute("DELETE FROM customer_profiles")
    db.commit()


def assert_node(result, expected_node, test_name):
    actual = result.get("current_node", result.get("current_step", ""))
    assert actual == expected_node, \
        f"[{test_name}] 期望 {expected_node}，实际 {actual}"


def assert_eligible(result, expected, test_name):
    actual = result.get("eligibility", {}).get("eligible", False)
    assert actual == expected, \
        f"[{test_name}] 期望 eligible={expected}，实际 {actual}"


def assert_escalate(result, expected, test_name):
    actual = result.get("eligibility", {}).get("escalate", False)
    assert actual == expected, \
        f"[{test_name}] 期望 escalate={expected}，实际 {actual}"


def assert_depreciation(result, expected, test_name):
    actual = result.get("eligibility", {}).get("depreciation", False)
    assert actual == expected, \
        f"[{test_name}] 期望 depreciation={expected}，实际 {actual}"


def run_all_tests():
    passed = 0
    failed = 0
    tests = [
        ("H001 正常退货", test_h001),
        ("H002 质量问题", test_h002),
        ("H003 超7天升级", test_h003),
        ("H004 内衣拒退", test_h004),
        ("H005 已拆封折旧", test_h005),
        ("H006 重复退货", test_h006),
        ("H007 金额超限", test_h007),
        ("H008 换货场景", test_h008),
        ("H009 售前咨询", test_h009_presale),
        ("H010 订单追踪", test_h010_track),
        ("H011 投诉升级", test_h011_complaint),
    ]
    for name, fn in tests:
        try:
            setup()
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过 / {failed} 失败 / {passed+failed} 总计")
    return passed == len(tests)


# ═══ 退货测试 (8条) ════════════════════════════════

def _return_agent():
    return FullAgent()


def test_h001():
    agent = _return_agent()
    r = agent.start("尺码不对太大了想退货", "ORD001")
    assert_node(r, "query_order", "H001")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["intent"]["intent_id"] == "INTENT_01_WRONG_SIZE"
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, True, "H001")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["refund"]["refund_amount"] > 0
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "CONFIRM_ACTION", {"idempotency_key": "H001"})
    assert "退货成功" in r.get("resolution", "")


def test_h002():
    agent = _return_agent()
    r = agent.start("手机屏幕碎了质量有问题", "ORD002")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["intent"]["intent_id"] == "INTENT_02_QUALITY_DEFECT"
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["refund"]["action"] == "full_refund"
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "CONFIRM_ACTION", {"idempotency_key": "H002"})
    assert "退货成功" in r.get("resolution", "")


def test_h003():
    agent = _return_agent()
    r = agent.start("鞋子不合脚想退", "ORD003")
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, False, "H003")
    assert_escalate(r, True, "H003")
    r = agent.event(r["session_id"], "CONTINUE")


def test_h004():
    agent = _return_agent()
    r = agent.start("内衣不合适想退", "ORD004")
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, False, "H004")
    assert any("不支持" in c.get("reason", "")
               for c in r["eligibility"].get("checks", []))


def test_h005():
    agent = _return_agent()
    r = agent.start("耳机不太喜欢", "ORD005", package_status="opened_intact")
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, True, "H005")
    assert_depreciation(r, True, "H005")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["refund"]["refund_amount"] < 499.00
    assert "折旧" in r["refund"]["notes"]
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "CONFIRM_ACTION", {"idempotency_key": "H005"})


def test_h006():
    agent = _return_agent()
    r = agent.start("想退货", "ORD006")
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, False, "H006")
    assert any("30天" in c.get("reason", "")
               for c in r["eligibility"].get("checks", []))


def test_h007():
    agent = _return_agent()
    r = agent.start("相机不太会操作", "ORD007")
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    assert_eligible(r, False, "H007")
    assert_escalate(r, True, "H007")


def test_h008():
    agent = _return_agent()
    r = agent.start("裤子大了尺码不对", "ORD008")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["intent"]["intent_id"] == "INTENT_01_WRONG_SIZE"
    r = agent.event(r["session_id"], "USER_ACCEPTED")
    r = agent.event(r["session_id"], "CONTINUE")
    assert r["refund"]["action"] in ("refund_minus_shipping", "exchange")
    assert "换货" in r["refund"]["notes"] or "运费" in r["refund"]["notes"]
    r = agent.event(r["session_id"], "CONTINUE")
    r = agent.event(r["session_id"], "CONFIRM_ACTION", {"idempotency_key": "H008"})


# ═══ 新场景测试 (3条) ═════════════════════════════

def test_h009_presale():
    """H009: 售前咨询 — 自动收集需求并推荐"""
    agent = FullAgent()
    r = agent.start("有没有蓝牙耳机推荐")
    assert r["scene"] == "presale", f"期望 presale，实际 {r['scene']}"
    # start 只执行 collect_needs，推荐在 CONTINUE 后
    r = agent.event(r["session_id"], "CONTINUE")
    assert len(r.get("matches", [])) > 0, "应该有匹配商品"
    assert r.get("talk_script", {}).get("script", "") != ""


def test_h010_track():
    """H010: 订单追踪 — 查物流"""
    agent = FullAgent()
    r = agent.start("快递到哪了查一下物流", "ORD001")
    assert r["scene"] == "order_track", f"期望 order_track，实际 {r['scene']}"
    assert r.get("talk_script", {}).get("script", "") != ""


def test_h011_complaint():
    """H011: 投诉升级 — 客服态度差"""
    agent = FullAgent()
    r = agent.start("你们客服态度太差了我要投诉")
    assert r["scene"] == "complaint", f"期望 complaint，实际 {r['scene']}"
    assert r.get("talk_script", {}).get("script", "") != ""


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

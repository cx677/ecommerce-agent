#!/usr/bin/env python3
"""
电商全场景智能体 · 主入口

场景：退货退款 | 售前咨询 | 订单追踪 | 投诉升级

用法：
  python main.py start --text "尺码不对想退货" --order ORD001
  python main.py event --session <id> --event CONTINUE
  python main.py status --session <id>
  python main.py test
  python main.py demo
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.orchestrator import FullAgent


def cmd_start(args):
    agent = FullAgent()
    result = agent.start(
        user_text=args.text,
        order_id=args.order or "",
        package_status=getattr(args, "package_status", "unopened"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_event(args):
    agent = FullAgent()
    result = agent.event(args.session, args.event,
                         json.loads(args.payload) if args.payload else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_status(args):
    agent = FullAgent()
    result = agent.status(args.session)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_demo():
    """快速演示 4 场景"""
    agent = FullAgent()

    demos = [
        ("退货退款", "尺码不对太大了想退货", "ORD001"),
        ("售前咨询", "有没有蓝牙耳机推荐", ""),
        ("订单追踪", "快递到哪了查一下ORD001物流", "ORD001"),
        ("投诉升级", "你们的客服态度太差了我要投诉", ""),
    ]

    for title, text, oid in demos:
        print(f"\n{'='*55}")
        print(f"  {title}: 「{text}」")
        r = agent.start(text, order_id=oid)
        print(f"  场景: {r['scene']} | 话术: {r.get('talk_script',{}).get('script','')[:100]}")

        if r.get("scene") == "return_refund":
            r = agent.event(r["session_id"], "CONTINUE")
            r = agent.event(r["session_id"], "USER_ACCEPTED")
            if r.get("eligibility", {}).get("eligible"):
                r = agent.event(r["session_id"], "CONTINUE")
                r = agent.event(r["session_id"], "CONTINUE")
                r = agent.event(r["session_id"], "CONFIRM_ACTION",
                                {"idempotency_key": f"DEMO-{oid}"})
                print(f"  结果: {r.get('resolution','')[:80]}")
            else:
                print(f"  结果: {r.get('talk_script',{}).get('script','')[:80]}")
        else:
            print(f"  步骤: {r.get('current_step','')}")


def cmd_test():
    from tests.test_golden_paths import run_all_tests
    run_all_tests()


def main():
    parser = argparse.ArgumentParser(description="电商全场景智能体")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("start")
    p.add_argument("--text", required=True)
    p.add_argument("--order", default="")
    p.add_argument("--package-status", default="unopened")

    p = sub.add_parser("event")
    p.add_argument("--session", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--payload")

    p = sub.add_parser("status")
    p.add_argument("--session", required=True)

    sub.add_parser("test")
    sub.add_parser("demo")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "event":
        cmd_event(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "test":
        cmd_test()
    elif args.command == "demo":
        cmd_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

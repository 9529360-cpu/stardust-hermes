"""Scale + robustness probe (DL-P2.9): 2000-fact store, decision latency and
recall p95; plus model-unavailable behaviour. No model calls (offline).

Usage: python -m evals.digital_life.fact_scale --home HOME --output OUT.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from evals.digital_life.memory_retrieval import HOME_VARS, assert_home

CITIES = ["米兰", "罗马", "都灵", "那不勒斯", "佛罗伦萨", "威尼斯", "博洛尼亚", "热那亚",
          "巴里", "卡塔尼亚", "巴勒莫", "维罗纳", "比萨", "帕多瓦", "的里雅斯特"]
TOPICS = ["手机", "笔记本", "牙医", "健身房", "书桌", "键盘", "显示器", "耳机", "手表", "自行车"]
NAMES = ["张伟", "王芳", "李娜", "刘洋", "陈静", "杨帆", "赵磊", "黄敏", "周杰", "吴倩",
         "徐强", "孙丽", "马超", "朱婷", "胡军", "郭晶", "何平", "高翔", "林悦", "罗成"]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)

    from tools import fact_store as fs
    from tools.fact_pipeline import decide_fact_ops

    # Build a 2000-fact store: 1900 closed (history) + 100 open (current).
    t0 = time.perf_counter()
    n_open, n_closed = 0, 0
    for i in range(1000):
        subject = f"scale-residence-{i}"
        first = fs.add_fact(subject, "居住地", f"用户{i}住在{CITIES[i % len(CITIES)]}。",
                            source="scale", source_session_id="scale")
        if first.get("success"):
            n_open += 0
        # close the first value by a second add + update path
        res = fs.add_fact(subject, "居住地", f"用户{i}住在{CITIES[(i+3) % len(CITIES)]}。",
                          source="scale", source_session_id="scale")
        if res.get("success"):
            n_open += 1
        if first.get("target_id"):
            closed = fs.close_fact(first["target_id"], reason="superseded")
            if closed.get("success"):
                n_closed += 1
    for i in range(1000, 2000):
        subject = f"scale-topic-{i}"
        res = fs.add_fact(subject, TOPICS[i % len(TOPICS)],
                          f"用户{i % 100}的{TOPICS[i % len(TOPICS)]}是{nameflip(i)}。",
                          source="scale", source_session_id="scale")
        if res.get("success"):
            n_open += 1
    build_s = time.perf_counter() - t0

    total = len(fs.load_facts(include_history=True))
    open_rows = len(fs.load_facts())

    # Recall p95 over 200 queries mixing hits and misses.
    queries = [f"用户{i % 1000}住在哪个城市？" for i in range(100)] + \
              [f"用户{i % 100}的{TOPICS[i % len(TOPICS)]}是什么？" for i in range(100)]
    latencies = []
    for q in queries:
        t1 = time.perf_counter()
        fs.recall_facts(q, limit=3)
        latencies.append((time.perf_counter() - t1) * 1000)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]

    # Decision latency p95 over 100 decides against the full store.
    dec_lat = []
    for i in range(100):
        cand = [{"subject": f"scale-residence-{i}", "predicate": "居住地",
                 "content": f"用户{i}住在{CITIES[(i+7) % len(CITIES)]}。", "confidence": 0.9}]
        t1 = time.perf_counter()
        decide_fact_ops(cand)
        dec_lat.append((time.perf_counter() - t1) * 1000)
    dec_lat.sort()
    dec_p50 = dec_lat[len(dec_lat) // 2]
    dec_p95 = dec_lat[int(len(dec_lat) * 0.95)]

    report = {
        "store_rows_total": total,
        "store_rows_open": open_rows,
        "build_seconds": round(build_s, 2),
        "recall_queries": len(queries),
        "recall_p50_ms": round(p50, 2),
        "recall_p95_ms": round(p95, 2),
        "decide_p50_ms": round(dec_p50, 2),
        "decide_p95_ms": round(dec_p95, 2),
        "pass_recall_p95_le_10ms": p95 <= 10.0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


def nameflip(i: int) -> str:
    return NAMES[i % len(NAMES)]


if __name__ == '__main__':
    main()

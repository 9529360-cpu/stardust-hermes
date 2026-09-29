"""Command line for the assistant-scenario exam.

    python -m evals.assistant_scenarios --rehearsal                  # scripted model, costs nothing
    python -m evals.assistant_scenarios --from-my-config             # the main model configured in Stardust
    python -m evals.assistant_scenarios --base-url URL --model NAME --api-key-env VAR

Exit code: 0 every scored scenario passed every run, 1 something failed, 2 usage error, 3 nothing could
be scored (the exam itself broke).
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from evals.assistant_scenarios import rehearsal, report, runner, sandbox
from evals.assistant_scenarios.fake_model import FakeModel
from evals.assistant_scenarios.scenarios import SCENARIOS, Scenario, by_id

REHEARSAL_LABEL = "彩排（剧本模型，不花钱）"


class UsageError(ValueError):
    pass


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.assistant_scenarios",
                                     description="星尘助理考卷：用真模型把 #16 的 8 个场景考一遍。")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--rehearsal", action="store_true", help="用剧本模型彩排，只检查考场本身（不花钱）")
    choice.add_argument("--from-my-config", action="store_true", help="用你在星尘里配置的主模型")
    choice.add_argument("--base-url", help="任意 OpenAI 兼容接口，例如 https://api.deepseek.com/v1")
    parser.add_argument("--model", help="配合 --base-url：模型名")
    parser.add_argument("--api-key-env", help="配合 --base-url：存放密钥的环境变量名（密钥本身不要写在命令里）")
    parser.add_argument("--api-mode", default="chat_completions", help="接口类型，默认 chat_completions")
    parser.add_argument("--context-length", type=int, help="模型上下文长度（可选）")
    parser.add_argument("--only", help="只考这些题，例如 1,4,8")
    parser.add_argument("--repeat", type=int, default=1, help="每题考几次（默认 1）")
    parser.add_argument("--out", help="成绩和证据的保存位置（默认系统临时目录）")
    parser.add_argument("--keep", action="store_true", help="保留每题的临时环境，方便排查")
    args = parser.parse_args(argv)
    if args.base_url and not args.model:
        parser.error("--base-url 需要同时给 --model")
    if args.repeat < 1:
        parser.error("--repeat 至少为 1")
    return args


def selected(args: argparse.Namespace) -> List[Scenario]:
    if not args.only:
        return list(SCENARIOS)
    ids = sorted({int(x) for x in args.only.replace("，", ",").split(",") if x.strip()})
    known = {s.id for s in SCENARIOS}
    if unknown := [i for i in ids if i not in known]:
        raise UsageError(f"没有这些题号：{unknown}（只有 1-8）")
    return [by_id(i) for i in ids]


def resolve_endpoint(args: argparse.Namespace, environ: Mapping[str, str]) -> sandbox.ModelEndpoint:
    if args.from_my_config:
        return _configured_main_model()
    key = ""
    if args.api_key_env:
        key = environ.get(args.api_key_env, "")
        if not key:
            raise UsageError(f"环境变量 {args.api_key_env} 里没有密钥")
    return sandbox.ModelEndpoint(base_url=args.base_url, model=args.model, api_mode=args.api_mode, api_key=key,
                                 context_length=args.context_length,
                                 label=f"{args.model} @ {urlparse(args.base_url).netloc}")


def _configured_main_model() -> sandbox.ModelEndpoint:
    """The main model exactly as Stardust itself resolves it (config + credential ladder)."""
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    model_cfg = load_config().get("model") or {}
    model = (model_cfg if isinstance(model_cfg, str) else model_cfg.get("default") or model_cfg.get("model") or "")
    try:
        runtime = resolve_runtime_provider()
    except Exception as exc:
        raise UsageError(f"没能解析你配置的主模型：{exc}") from exc
    if not model or not runtime.get("base_url"):
        raise UsageError("没找到你配置的主模型，请先在星尘里设置模型，或改用 --base-url")
    return sandbox.ModelEndpoint(base_url=runtime["base_url"], model=str(model),
                                 api_mode=runtime.get("api_mode") or "chat_completions",
                                 api_key=runtime.get("api_key") or "",
                                 label=f"{model} @ {urlparse(runtime['base_url']).netloc}")


def _run_all(chosen: List[Scenario], repeat: int, config_for) -> List[runner.ScenarioResult]:
    results, total = [], len(chosen) * repeat
    for n in range(1, repeat + 1):
        cfg = config_for(n)
        for scenario in chosen:
            print(f"[{len(results) + 1}/{total}] 第 {scenario.id} 题 {scenario.title} …", flush=True)
            res = runner.run_scenario(scenario, cfg)
            print(f"      → {report.summarize([res])[0].label()}（{res.wall_s:.0f} 秒）", flush=True)
            results.append(res)
    return results


def _out_for(out_dir: Path, repeat: int, n: int) -> Path:
    return out_dir / f"run{n}" if repeat > 1 else out_dir


def main(argv: Optional[Sequence[str]] = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")  # a GBK console must not crash the report
    args = parse_args(argv)
    out_dir = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"stardust-exam-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        chosen = selected(args)
        if args.rehearsal:
            label = REHEARSAL_LABEL
            with FakeModel(rehearsal.play) as model:
                results = _run_all(chosen, args.repeat, lambda n: rehearsal.config(
                    model, out_dir, out_dir=_out_for(out_dir, args.repeat, n), work_root=None, keep=args.keep))
        else:
            endpoint = resolve_endpoint(args, os.environ)
            label = endpoint.label
            results = _run_live(chosen, args, endpoint, out_dir)
    except UsageError as exc:
        print(f"用法错误：{exc}", file=sys.stderr)
        return 2
    summaries = report.summarize(results)
    report.write_json(summaries, out_dir / "report.json", model_label=label)
    text = report.render_text(summaries, model_label=label, when=time.strftime("%Y-%m-%d %H:%M"), out_dir=out_dir)
    (out_dir / "report.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    if not any(s.scored for s in summaries):
        return 3
    return 0 if all(s.stable for s in summaries if s.scored) else 1


def _run_live(chosen: List[Scenario], args: argparse.Namespace, endpoint: sandbox.ModelEndpoint,
              out_dir: Path) -> List[runner.ScenarioResult]:
    print(f"将用 {endpoint.label} 考 {len(chosen)} 道题 × {args.repeat} 次。每题都在独立的临时环境里进行，"
          f"不碰你的真实聊天、记忆和定时任务。", flush=True)
    tool_root = Path(tempfile.mkdtemp(prefix="stardust-exam-python-"))
    try:
        tool_bin = sandbox.make_tool_python(tool_root / "venv", sys.executable)
        return _run_all(chosen, args.repeat, lambda n: runner.RunConfig(
            endpoint=endpoint, out_dir=_out_for(out_dir, args.repeat, n), tool_bin=tool_bin, keep=args.keep))
    finally:
        shutil.rmtree(tool_root, ignore_errors=True)

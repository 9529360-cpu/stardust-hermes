"""Deterministic, opt-in planning for Stardust web search.

The planner never calls a model and never mutates the prompt/tool surface. The legacy strategy is
the default so existing installations keep today's cost/latency semantics. Adaptive mode is enabled
explicitly under web.search_strategy and decides whether an already-configured ensemble is worth
paying for based on the user's query shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


SearchIntent = Literal[
    "simple",
    "current",
    "news",
    "comparison",
    "verification",
    "deep_research",
    "navigational",
    "technical",
]
SearchMode = Literal["single", "ensemble"]

_URLISH_RE = re.compile(
    r"(?i)(?:https?://|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?\b)"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.-]+|[\u3400-\u9fff]+")
_CURRENT = (
    "latest", "current", "today", "recent", "newest", "this week", "this month",
    "最新", "现在", "当前", "今天", "近期", "最近", "本周", "本月",
)
_NEWS = ("news", "breaking", "announcement", "announced", "新闻", "突发", "宣布", "最新消息")
_COMPARE = (
    " vs ", " versus ", " compare", " comparison", " difference", " differences",
    "对比", "比较", "区别", "差别", "相比", "哪个好", "哪一个好",
)
_VERIFY = (
    "verify", "fact check", "confirm", "is it true", "true or false", "accurate",
    "核实", "验证", "确认", "真的假的", "是否属实", "是真的吗", "真不真",
)
_DEEP = (
    "research", "investigate", "deep dive", "comprehensive", "in-depth", "overview",
    "调研", "研究", "调查", "深入", "全面", "详细分析", "深度",
)
_NAV = (
    "official site", "official website", "homepage", "官网", "官方网站", "主页",
)
_TECH = (
    "documentation", "docs", "api reference", "stack trace", "traceback", "error code",
    "文档", "接口文档", "报错", "错误码", "堆栈",
)


@dataclass(frozen=True)
class SearchPlan:
    strategy: str
    intent: SearchIntent
    mode: SearchMode
    freshness: bool = False
    diversity: bool = False
    near_duplicate_dedupe: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "intent": self.intent,
            "mode": self.mode,
            "freshness": self.freshness,
            "diversity": self.diversity,
            "near_duplicate_dedupe": self.near_duplicate_dedupe,
            "reason": self.reason,
        }


def _contains_any(query: str, needles: tuple[str, ...]) -> bool:
    text = query.lower()
    for needle in needles:
        if not needle:
            continue
        if needle.isascii() and needle == needle.strip():
            if re.search(r"(?<!\\w)" + re.escape(needle) + r"(?!\\w)", text):
                return True
        elif needle in text:
            return True
    return False


def classify_search_intent(query: str) -> SearchIntent:
    """Classify only search-routing needs; this is not the assistant intent classifier."""
    text = str(query or "").strip()
    lowered = text.lower()

    if _contains_any(lowered, _VERIFY):
        return "verification"
    if _contains_any(lowered, _COMPARE):
        return "comparison"
    if _contains_any(lowered, _DEEP):
        return "deep_research"
    if _contains_any(lowered, _NEWS):
        return "news"
    if _contains_any(lowered, _CURRENT):
        return "current"
    if _contains_any(lowered, _NAV) or (
        _URLISH_RE.search(text) is not None and len(_TOKEN_RE.findall(text)) <= 6
    ):
        return "navigational"
    if _contains_any(lowered, _TECH):
        return "technical"
    return "simple"


def configured_search_strategy(config: dict[str, Any] | None) -> str:
    """Return legacy/adaptive/single/ensemble; invalid values fail safely to legacy."""
    raw = str((config or {}).get("search_strategy") or "legacy").strip().lower()
    return raw if raw in {"legacy", "adaptive", "single", "ensemble"} else "legacy"


def plan_search(query: str, config: dict[str, Any] | None, *, ensemble_available: bool) -> SearchPlan:
    """Return one bounded execution plan without performing network work."""
    strategy = configured_search_strategy(config)
    intent = classify_search_intent(query)

    if strategy == "single" or not ensemble_available:
        return SearchPlan(
            strategy=strategy,
            intent=intent,
            mode="single",
            freshness=intent in {"current", "news"},
            reason="ensemble disabled or unavailable",
        )

    if strategy in {"legacy", "ensemble"}:
        return SearchPlan(
            strategy=strategy,
            intent=intent,
            mode="ensemble",
            freshness=intent in {"current", "news"},
            diversity=False,
            near_duplicate_dedupe=False,
            reason="configured ensemble behavior preserved",
        )

    ensemble_intents = {"current", "news", "comparison", "verification", "deep_research"}
    use_ensemble = intent in ensemble_intents
    return SearchPlan(
        strategy="adaptive",
        intent=intent,
        mode="ensemble" if use_ensemble else "single",
        freshness=intent in {"current", "news"},
        diversity=use_ensemble,
        near_duplicate_dedupe=use_ensemble,
        reason=(
            "independent coverage/corroboration is useful for this query"
            if use_ensemble
            else "one provider is sufficient for this query shape"
        ),
    )

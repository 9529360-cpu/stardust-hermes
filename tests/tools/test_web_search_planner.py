from tools.web_search_planner import (
    classify_search_intent,
    configured_search_strategy,
    plan_search,
)


def test_strategy_defaults_to_legacy_for_backward_compatibility():
    assert configured_search_strategy({}) == "legacy"
    assert configured_search_strategy({"search_strategy": "bogus"}) == "legacy"


def test_legacy_preserves_configured_ensemble_behavior():
    plan = plan_search("python dataclasses", {}, ensemble_available=True)
    assert plan.strategy == "legacy"
    assert plan.mode == "ensemble"
    assert plan.diversity is False


def test_adaptive_keeps_simple_and_technical_queries_single_provider():
    simple = plan_search(
        "python dataclass syntax",
        {"search_strategy": "adaptive"},
        ensemble_available=True,
    )
    technical = plan_search(
        "Python traceback documentation",
        {"search_strategy": "adaptive"},
        ensemble_available=True,
    )
    assert simple.intent == "simple"
    assert simple.mode == "single"
    assert technical.intent == "technical"
    assert technical.mode == "single"


def test_adaptive_ensembles_current_comparison_verification_and_deep_research():
    queries = [
        ("latest OpenAI API changes", "current"),
        ("Postgres vs MySQL comparison", "comparison"),
        ("verify whether this announcement is true", "verification"),
        ("deep research on vector databases", "deep_research"),
        ("最新的 Python 安全新闻", "news"),
        ("核实这个消息是否属实", "verification"),
        ("深入调研 MCP 生态", "deep_research"),
    ]
    for query, intent in queries:
        plan = plan_search(
            query,
            {"search_strategy": "adaptive"},
            ensemble_available=True,
        )
        assert plan.intent == intent
        assert plan.mode == "ensemble"
        assert plan.diversity is True
        assert plan.near_duplicate_dedupe is True


def test_adaptive_current_queries_enable_freshness_signal():
    plan = plan_search(
        "what changed today in Python",
        {"search_strategy": "adaptive"},
        ensemble_available=True,
    )
    assert plan.intent == "current"
    assert plan.freshness is True


def test_deep_research_can_independently_request_freshness():
    plan = plan_search(
        "deep research on the latest MCP changes",
        {"search_strategy": "adaptive"},
        ensemble_available=True,
    )
    assert plan.intent == "deep_research"
    assert plan.mode == "ensemble"
    assert plan.freshness is True


def test_adaptive_does_not_invent_ensemble_when_no_extra_provider_exists():
    plan = plan_search(
        "latest security news",
        {"search_strategy": "adaptive"},
        ensemble_available=False,
    )
    assert plan.intent == "news"
    assert plan.mode == "single"


def test_explicit_single_and_ensemble_override_adaptive_decision():
    single = plan_search(
        "latest security news",
        {"search_strategy": "single"},
        ensemble_available=True,
    )
    ensemble = plan_search(
        "python dataclass syntax",
        {"search_strategy": "ensemble"},
        ensemble_available=True,
    )
    assert single.mode == "single"
    assert ensemble.mode == "ensemble"


def test_navigational_url_query_stays_single_in_adaptive_mode():
    assert classify_search_intent("https://docs.python.org") == "navigational"
    plan = plan_search(
        "https://docs.python.org",
        {"search_strategy": "adaptive"},
        ensemble_available=True,
    )
    assert plan.mode == "single"


def test_english_keywords_do_not_match_inside_larger_words():
    assert classify_search_intent("currently implementing a parser") == "simple"
    assert classify_search_intent("newspaper parser library") == "simple"

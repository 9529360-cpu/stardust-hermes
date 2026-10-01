from tools.tool_search_catalog import build_catalog, search_catalog


def _td(name: str, description: str):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_score_weights_demote_degraded_backend_without_hiding_it():
    catalog = build_catalog([
        _td("mcp__alpha__create_issue", "Create an issue"),
        _td("mcp__beta__create_issue", "Create an issue"),
    ])
    hits = search_catalog(
        catalog, "create issue", limit=2,
        score_weights={
            "mcp__alpha__create_issue": 0.05,
            "mcp__beta__create_issue": 1.0,
        },
    )
    assert [h.name for h in hits] == [
        "mcp__beta__create_issue",
        "mcp__alpha__create_issue",
    ]


def test_exact_name_lookup_stays_authoritative_despite_health_weight():
    catalog = build_catalog([
        _td("mcp__alpha__create_issue", "Create an issue"),
        _td("mcp__beta__create_issue", "Create an issue"),
    ])
    hits = search_catalog(
        catalog, "mcp__alpha__create_issue", limit=2,
        score_weights={
            "mcp__alpha__create_issue": 0.01,
            "mcp__beta__create_issue": 1.0,
        },
    )
    assert hits[0].name == "mcp__alpha__create_issue"

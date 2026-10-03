import datetime as dt

from tools.web_search_quality import rerank_fused_results


NOW = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def _row(title, url, score, *, first_seen=0, sources=None, published_date=None):
    row = {
        "title": title,
        "url": url,
        "description": title,
        "score": score,
        "first_seen": first_seen,
        "sources": sources or ["a"],
    }
    if published_date is not None:
        row["published_date"] = published_date
    return row


def test_undated_result_is_not_penalized_by_freshness():
    rows = [
        _row("Undated but higher RRF", "https://a.example/x", 1.00, first_seen=0),
        _row(
            "Fresh but lower RRF",
            "https://b.example/y",
            0.80,
            first_seen=1,
            published_date="2026-10-01",
        ),
    ]
    out = rerank_fused_results(rows, 2, freshness=True, now=NOW)
    assert out[0]["title"] == "Undated but higher RRF"


def test_close_rrf_scores_can_be_reordered_by_freshness_evidence():
    rows = [
        _row(
            "Old result",
            "https://a.example/x",
            1.00,
            first_seen=0,
            published_date="2024-01-01",
        ),
        _row(
            "Fresh result",
            "https://b.example/y",
            0.90,
            first_seen=1,
            published_date="2026-10-01",
        ),
    ]
    out = rerank_fused_results(rows, 2, freshness=True, now=NOW)
    assert out[0]["title"] == "Fresh result"
    assert out[0]["published_date"] == "2026-10-01"


def test_diversity_limits_one_host_to_two_until_other_hosts_are_used():
    rows = [
        _row("A1", "https://same.example/1", 1.0, first_seen=0),
        _row("A2", "https://same.example/2", 0.9, first_seen=1),
        _row("A3", "https://same.example/3", 0.8, first_seen=2),
        _row("B1", "https://other.example/1", 0.7, first_seen=3),
    ]
    out = rerank_fused_results(rows, 3, diversity=True, now=NOW)
    assert [r["title"] for r in out] == ["A1", "A2", "B1"]


def test_exact_long_title_duplicates_collapse_and_union_sources():
    title = "Major product announcement with the exact same headline"
    rows = [
        _row(title, "https://wire.example/a", 1.0, first_seen=0, sources=["a"]),
        _row(title, "https://copy.example/b", 0.9, first_seen=1, sources=["b"]),
        _row("Independent coverage", "https://news.example/c", 0.8, first_seen=2, sources=["c"]),
    ]
    out = rerank_fused_results(rows, 5, near_duplicate_dedupe=True, now=NOW)
    assert len(out) == 2
    assert out[0]["title"] == title
    assert out[0]["sources"] == ["a", "b"]


def test_short_generic_titles_do_not_collapse():
    rows = [
        _row("Docs", "https://a.example", 1.0, first_seen=0),
        _row("Docs", "https://b.example", 0.9, first_seen=1),
    ]
    out = rerank_fused_results(rows, 5, near_duplicate_dedupe=True, now=NOW)
    assert len(out) == 2

from __future__ import annotations

from api.routers.timeline import _collapse_failed_runs


def _failed_run(run_id: str, issue_id: str = "i1", detail: str = "agent: HTTP 429: rate limit") -> dict:
    return {"id": f"run:{run_id}", "issue_id": issue_id, "type": "run", "status": "failed", "title": "Run fallida", "detail": detail}


def _comment(comment_id: str, issue_id: str = "i1") -> dict:
    return {"id": f"comment:{comment_id}", "issue_id": issue_id, "type": "comment", "status": None, "title": "Comentario agente", "detail": "hola"}


def test_consecutive_rate_limit_failures_collapse() -> None:
    items = [_failed_run("a"), _failed_run("b"), _failed_run("c")]

    out = _collapse_failed_runs(items)

    assert len(out) == 1
    assert out[0]["count"] == 3
    assert "rate limit del proveedor (x3)" in out[0]["title"]


def test_other_item_breaks_the_group() -> None:
    items = [_failed_run("a"), _comment("x"), _failed_run("b")]

    out = _collapse_failed_runs(items)

    assert len(out) == 3
    assert out[0]["count"] == 1
    assert out[2]["count"] == 1


def test_different_cause_breaks_the_group() -> None:
    items = [
        _failed_run("a"),
        _failed_run("b", detail="agent: The read operation timed out"),
    ]

    out = _collapse_failed_runs(items)

    assert len(out) == 2
    assert "rate limit" in out[0]["title"]
    assert "timeout" in out[1]["title"]


def test_different_issue_breaks_the_group() -> None:
    items = [_failed_run("a", issue_id="i1"), _failed_run("b", issue_id="i2")]

    out = _collapse_failed_runs(items)

    assert len(out) == 2


def test_non_failed_runs_untouched() -> None:
    items = [
        {"id": "run:ok", "issue_id": "i1", "type": "run", "status": "completed", "title": "Run completada", "detail": "agent"},
        _comment("x"),
    ]

    out = _collapse_failed_runs(items)

    assert out == items

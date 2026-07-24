from __future__ import annotations

import sys

from scripts import validate_ecosystem_fixtures


def test_require_arguments_limit_the_executed_cases(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_validate(**kwargs):
        observed.update(kwargs)
        return {"cases": [], "summary": {}}

    monkeypatch.setattr(
        validate_ecosystem_fixtures,
        "validate_ecosystem_fixtures",
        fake_validate,
    )
    monkeypatch.setattr(
        validate_ecosystem_fixtures,
        "required_cases_satisfied",
        lambda receipt, required: (True, []),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_ecosystem_fixtures.py",
            "--require",
            "python_pytest",
            "--require",
            "javascript_npm",
        ],
    )

    assert validate_ecosystem_fixtures.main() == 0
    assert observed["selected_case_ids"] == [
        "python_pytest",
        "javascript_npm",
    ]

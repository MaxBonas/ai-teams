from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam.model_owner_preferences import (
    MODEL_OWNER_PREFERENCES_VERSION,
    ModelOwnerPreferencesError,
    append_model_owner_preferences,
    get_model_owner_preference,
    load_model_owner_preferences,
    replace_model_owner_preferences,
    set_model_owner_preference,
)

NOW = "2026-07-24T18:00:00+02:00"


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))


def test_missing_document_defaults_to_normal_without_writing() -> None:
    document = load_model_owner_preferences()
    preference = get_model_owner_preference(
        "codex_subscription",
        "gpt-5.6-sol",
    )

    assert document == {
        "schema_version": MODEL_OWNER_PREFERENCES_VERSION,
        "updated_at": None,
        "preferences": [],
    }
    assert preference["state"] == "normal"
    assert preference["source"] == "default"


def test_preferences_persist_exact_identity_in_stable_order(tmp_path: Path) -> None:
    set_model_owner_preference(
        "opencode_zen_free",
        "opencode/ling-3.0-flash-free",
        state="high",
        reason="prioridad owner",
        updated_at=NOW,
    )
    archived = set_model_owner_preference(
        "local_gem4_lmstudio",
        "gemma-3-4b-it",
        state="archived",
        reason="archivado por el owner",
        updated_at=NOW,
    )

    document = load_model_owner_preferences()
    assert archived["source"] == "user_machine"
    assert [
        (item["profile_id"], item["model_id"])
        for item in document["preferences"]
    ] == [
        ("local_gem4_lmstudio", "gemma-3-4b-it"),
        ("opencode_zen_free", "opencode/ling-3.0-flash-free"),
    ]
    path = tmp_path / "user-config" / "model_owner_preferences.json"
    assert json.loads(path.read_text(encoding="utf-8")) == document
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_same_model_slug_remains_separate_by_profile() -> None:
    set_model_owner_preference(
        "codex_subscription",
        "gpt-5.6-sol",
        state="high",
        reason="suscripción prioritaria",
        updated_at=NOW,
    )
    set_model_owner_preference(
        "openai_api",
        "gpt-5.6-sol",
        state="low",
        reason="API secundaria",
        updated_at=NOW,
    )

    assert get_model_owner_preference(
        "codex_subscription", "gpt-5.6-sol"
    )["state"] == "high"
    assert get_model_owner_preference(
        "openai_api", "gpt-5.6-sol"
    )["state"] == "low"


def test_reactivation_is_explicit_normal_state() -> None:
    set_model_owner_preference(
        "local_gem4_lmstudio",
        "gemma-3-12b-it",
        state="archived",
        reason="sin uso",
        updated_at=NOW,
    )
    restored = set_model_owner_preference(
        "local_gem4_lmstudio",
        "gemma-3-12b-it",
        state="normal",
        reason="reactivado por el owner",
        updated_at="2026-07-24T19:00:00+02:00",
    )

    assert restored["state"] == "normal"
    assert restored["reason"] == "reactivado por el owner"
    assert restored["source"] == "user_machine"
    assert get_model_owner_preference(
        "local_gem4_lmstudio", "gemma-3-12b-it"
    ) == restored


def test_clean_machine_restart_and_reactivation_are_portable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "portable-user-config"
    env = os.environ.copy()
    env["AITEAM_USER_CONFIG_DIR"] = str(config_dir)
    command = [
        sys.executable,
        "-c",
        (
            "from aiteam.model_owner_preferences import "
            "get_model_owner_preference; "
            "print(get_model_owner_preference("
            "'codex_subscription','gpt-5.6-sol')['state'])"
        ),
    ]

    clean = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert clean.stdout.strip() == "normal"
    assert not (config_dir / "model_owner_preferences.json").exists()

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(config_dir))
    set_model_owner_preference(
        "codex_subscription",
        "gpt-5.6-sol",
        state="archived",
        reason="persistencia entre procesos",
        updated_at=NOW,
    )
    restarted = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert restarted.stdout.strip() == "archived"

    set_model_owner_preference(
        "codex_subscription",
        "gpt-5.6-sol",
        state="normal",
        reason="reactivación explícita",
        updated_at="2026-07-24T19:00:00+02:00",
    )
    reactivated = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert reactivated.stdout.strip() == "normal"


def test_bulk_replacement_is_validated_sorted_and_atomic(tmp_path: Path) -> None:
    document = replace_model_owner_preferences(
        [
            {
                "profile_id": "profile-b",
                "model_id": "model-b",
                "state": "low",
                "reason": "resto",
            },
            {
                "profile_id": "profile-a",
                "model_id": "model-a",
                "state": "high",
                "reason": "prioridad",
            },
        ],
        updated_at=NOW,
    )

    assert [
        (item["profile_id"], item["model_id"], item["state"])
        for item in document["preferences"]
    ] == [
        ("profile-a", "model-a", "high"),
        ("profile-b", "model-b", "low"),
    ]
    path = tmp_path / "user-config" / "model_owner_preferences.json"
    assert json.loads(path.read_text(encoding="utf-8")) == document
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_bulk_replacement_rejects_duplicates_without_overwriting() -> None:
    set_model_owner_preference(
        "profile-a",
        "model-a",
        state="normal",
        reason="baseline",
        updated_at=NOW,
    )
    before = load_model_owner_preferences()

    with pytest.raises(
        ModelOwnerPreferencesError,
        match="duplicate profile_id/model_id preference",
    ):
        replace_model_owner_preferences(
            [
                {
                    "profile_id": "profile-a",
                    "model_id": "model-a",
                    "state": "high",
                    "reason": "uno",
                },
                {
                    "profile_id": "profile-a",
                    "model_id": "model-a",
                    "state": "low",
                    "reason": "dos",
                },
            ],
            updated_at=NOW,
        )

    assert load_model_owner_preferences() == before


def test_append_is_atomic_and_preserves_existing_entry() -> None:
    existing = set_model_owner_preference(
        "profile-a",
        "model-a",
        state="high",
        reason="baseline",
        updated_at=NOW,
    )

    document = append_model_owner_preferences(
        [
            {
                "profile_id": "profile-b",
                "model_id": "model-b",
                "state": "low",
                "reason": "residual",
            }
        ],
        updated_at="2026-07-30T12:00:00+00:00",
    )

    assert document["preferences"][0] == {
        key: existing[key]
        for key in ("profile_id", "model_id", "state", "reason", "updated_at")
    }
    assert document["preferences"][1]["state"] == "low"


def test_append_rejects_existing_identity_without_overwriting() -> None:
    set_model_owner_preference(
        "profile-a",
        "model-a",
        state="high",
        reason="baseline",
        updated_at=NOW,
    )
    before = load_model_owner_preferences()

    with pytest.raises(
        ModelOwnerPreferencesError,
        match="identity already exists",
    ):
        append_model_owner_preferences(
            [
                {
                    "profile_id": "profile-a",
                    "model_id": "model-a",
                    "state": "low",
                    "reason": "collision",
                }
            ]
        )

    assert load_model_owner_preferences() == before


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "profile_id": "",
                "model_id": "model",
                "state": "high",
                "reason": "owner",
            },
            "profile_id is required",
        ),
        (
            {
                "profile_id": "profile",
                "model_id": "model with spaces",
                "state": "high",
                "reason": "owner",
            },
            "model_id cannot contain whitespace",
        ),
        (
            {
                "profile_id": "profile",
                "model_id": "model",
                "state": "favorite",
                "reason": "owner",
            },
            "state must be high, normal, low or archived",
        ),
        (
            {
                "profile_id": "profile",
                "model_id": "model",
                "state": "low",
                "reason": "",
            },
            "reason is required",
        ),
    ],
)
def test_invalid_preference_is_rejected_without_creating_file(
    kwargs: dict[str, str],
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ModelOwnerPreferencesError, match=message):
        set_model_owner_preference(**kwargs, updated_at=NOW)

    assert not (
        tmp_path / "user-config" / "model_owner_preferences.json"
    ).exists()


def test_corrupt_document_fails_closed_and_is_not_overwritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "user-config" / "model_owner_preferences.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": "wrong", "preferences": []}', encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ModelOwnerPreferencesError):
        load_model_owner_preferences()
    with pytest.raises(ModelOwnerPreferencesError):
        set_model_owner_preference(
            "codex_subscription",
            "gpt-5.6-sol",
            state="high",
            reason="owner",
            updated_at=NOW,
        )

    assert path.read_bytes() == before

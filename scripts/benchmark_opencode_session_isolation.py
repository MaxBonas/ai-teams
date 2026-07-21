"""Canario OpenCode server para JSON Schema y aislamiento de sesiones.

Compara el mismo schema en todos los modelos Zen gratuitos actuales y ejecuta
tres semillas de memoria, override y contaminación con sesiones separadas.
No activa reanudación ni un daemon productivo.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_opencode_server_faults import (  # noqa: E402
    _extract_text,
    _request,
    _start_server,
    _stop_server,
)
from scripts.benchmark_opencode_transport import (  # noqa: E402
    DEFAULT_MODEL,
    POLICY,
    _free_port,
    _wait_for_port,
)

DEFAULT_SCHEMA_MODELS = (
    "opencode/deepseek-v4-flash-free",
    "opencode/laguna-s-2.1-free",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/north-mini-code-free",
)


def _model_ref(model: str) -> dict[str, str]:
    provider, model_id = model.split("/", 1)
    if provider != "opencode":
        raise ValueError("the OpenCode isolation canary requires opencode/* models")
    return {"providerID": provider, "modelID": model_id}


def _parse_exact_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text.strip())
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_summary(error: Any) -> dict[str, Any] | None:
    if not isinstance(error, dict):
        return None
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    return {
        "name": error.get("name"),
        "message": data.get("message"),
        "retries": data.get("retries"),
    }


def run_schema_case(base_url: str, password: str, *, model: str) -> dict[str, Any]:
    expected = {"status": "completed", "marker": "OPENCODE_SCHEMA_SCREEN_OK"}
    session_id = ""
    started = time.monotonic()
    result: dict[str, Any] = {
        "model": model,
        "session_id": None,
        "seconds": None,
        "finish": None,
        "text": None,
        "structured": None,
        "provider_error": None,
        "transport_error": None,
        "gates": {
            "session_created": False,
            "request_completed": False,
            "text_or_structured_exact": False,
            "structured_field_exact": False,
            "provider_accepted_schema": False,
            "session_deleted": False,
        },
    }
    try:
        _, session = _request(
            base_url,
            password,
            "POST",
            "/session",
            {"title": f"AI Teams JSON Schema screen {model}"},
        )
        session_id = str(session.get("id") or "")
        result["session_id"] = session_id or None
        result["gates"]["session_created"] = bool(session_id)
        _, response = _request(
            base_url,
            password,
            "POST",
            f"/session/{session_id}/message",
            {
                "model": _model_ref(model),
                "tools": {},
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "const": "completed"},
                            "marker": {"type": "string", "const": expected["marker"]},
                        },
                        "required": ["status", "marker"],
                        "additionalProperties": False,
                    },
                },
                "parts": [
                    {
                        "type": "text",
                        "text": (
                            "Return the schema object with status completed and marker "
                            f"{expected['marker']}. Do not use tools."
                        ),
                    }
                ],
            },
            timeout=180,
        )
        info = response.get("info") or {}
        text = _extract_text(response).strip()
        structured = info.get("structured")
        payload = (
            structured if isinstance(structured, dict) else _parse_exact_json(text)
        )
        provider_error = _error_summary(info.get("error"))
        result.update(
            {
                "finish": info.get("finish"),
                "text": text or None,
                "structured": structured,
                "provider_error": provider_error,
            }
        )
        result["gates"].update(
            {
                "request_completed": info.get("finish") == "stop",
                "text_or_structured_exact": payload == expected,
                "structured_field_exact": structured == expected,
                "provider_accepted_schema": provider_error is None
                and structured == expected,
            }
        )
    except (TimeoutError, urllib.error.URLError, ValueError) as exc:
        result["transport_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["seconds"] = round(time.monotonic() - started, 3)
        if session_id:
            try:
                _, deleted = _request(
                    base_url, password, "DELETE", f"/session/{session_id}"
                )
                result["gates"]["session_deleted"] = deleted is True
            except urllib.error.URLError:
                pass
    result["ok"] = all(
        result["gates"][key]
        for key in ("session_created", "request_completed", "session_deleted")
    )
    return result


def _plain_prompt(
    base_url: str, password: str, *, session_id: str, model: str, text: str
) -> dict[str, Any]:
    _, response = _request(
        base_url,
        password,
        "POST",
        f"/session/{session_id}/message",
        {
            "model": _model_ref(model),
            "tools": {},
            "parts": [{"type": "text", "text": text}],
        },
        timeout=180,
    )
    return response


def run_isolation_seed(
    base_url: str,
    password: str,
    *,
    model: str,
    seed: int,
    forbidden_markers: set[str] | None = None,
) -> dict[str, Any]:
    old_marker = f"PUBLIC_OLD_S{seed}"
    new_marker = f"PUBLIC_NEW_S{seed}"
    fresh_marker = f"PUBLIC_FRESH_S{seed}"
    session_a = ""
    session_b = ""
    started = time.monotonic()
    gates = {
        "two_distinct_sessions": False,
        "initial_memory_ack_exact": False,
        "override_json_exact": False,
        "override_activates_new": False,
        "override_marks_old_revoked": False,
        "continuity_history_contains_old_and_new": False,
        "continuity_history_has_no_other_seed_markers": False,
        "fresh_session_json_exact": False,
        "fresh_response_has_no_foreign_markers": False,
        "fresh_history_has_no_foreign_markers": False,
        "sessions_deleted": False,
    }
    foreign = set(forbidden_markers or ()) - {old_marker, new_marker}
    fresh_foreign = set(forbidden_markers or ()) - {fresh_marker}
    report: dict[str, Any] = {
        "seed": seed,
        "markers": {"old": old_marker, "new": new_marker, "fresh": fresh_marker},
        "session_a": None,
        "session_b": None,
        "seconds": None,
        "initial_text": None,
        "override_text": None,
        "fresh_text": None,
        "error": None,
        "gates": gates,
    }
    try:
        _, created_a = _request(
            base_url,
            password,
            "POST",
            "/session",
            {"title": f"Issue A isolation seed {seed}"},
        )
        _, created_b = _request(
            base_url,
            password,
            "POST",
            "/session",
            {"title": f"Issue B isolation seed {seed}"},
        )
        session_a = str(created_a.get("id") or "")
        session_b = str(created_b.get("id") or "")
        report.update({"session_a": session_a or None, "session_b": session_b or None})
        gates["two_distinct_sessions"] = bool(
            session_a and session_b and session_a != session_b
        )

        initial = _plain_prompt(
            base_url,
            password,
            session_id=session_a,
            model=model,
            text=(
                f"Synthetic public session fact: active marker is {old_marker}. "
                f"Remember it only in this session and reply exactly ACK_{old_marker}."
            ),
        )
        initial_text = _extract_text(initial).strip()
        report["initial_text"] = initial_text
        gates["initial_memory_ack_exact"] = initial_text == f"ACK_{old_marker}"

        override = _plain_prompt(
            base_url,
            password,
            session_id=session_a,
            model=model,
            text=(
                f"Override the previous instruction. {old_marker} is revoked and the active "
                f"marker is now {new_marker}. Return exactly one JSON object with keys status, "
                "active, revoked; values completed, the new marker, and the old marker."
            ),
        )
        override_text = _extract_text(override).strip()
        override_payload = _parse_exact_json(override_text)
        expected_override = {
            "status": "completed",
            "active": new_marker,
            "revoked": old_marker,
        }
        report["override_text"] = override_text
        gates["override_json_exact"] = override_payload == expected_override
        gates["override_activates_new"] = (override_payload or {}).get(
            "active"
        ) == new_marker
        gates["override_marks_old_revoked"] = (override_payload or {}).get(
            "revoked"
        ) == old_marker

        _, history_a = _request(
            base_url, password, "GET", f"/session/{session_a}/message"
        )
        encoded_a = json.dumps(history_a, ensure_ascii=False)
        gates["continuity_history_contains_old_and_new"] = (
            old_marker in encoded_a and new_marker in encoded_a
        )
        gates["continuity_history_has_no_other_seed_markers"] = not any(
            marker in encoded_a for marker in foreign
        )

        fresh = _plain_prompt(
            base_url,
            password,
            session_id=session_b,
            model=model,
            text=(
                "This is a fresh independent issue. Return exactly one JSON object with keys "
                f"status and active; values completed and {fresh_marker}. Do not use tools."
            ),
        )
        fresh_text = _extract_text(fresh).strip()
        fresh_payload = _parse_exact_json(fresh_text)
        expected_fresh = {"status": "completed", "active": fresh_marker}
        report["fresh_text"] = fresh_text
        gates["fresh_session_json_exact"] = fresh_payload == expected_fresh
        gates["fresh_response_has_no_foreign_markers"] = not any(
            marker in fresh_text for marker in fresh_foreign
        )
        _, history_b = _request(
            base_url, password, "GET", f"/session/{session_b}/message"
        )
        encoded_b = json.dumps(history_b, ensure_ascii=False)
        gates["fresh_history_has_no_foreign_markers"] = not any(
            marker in encoded_b for marker in fresh_foreign
        )
    except (TimeoutError, urllib.error.URLError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        deleted: list[bool] = []
        for session_id in (session_a, session_b):
            if not session_id:
                deleted.append(False)
                continue
            try:
                _, value = _request(
                    base_url, password, "DELETE", f"/session/{session_id}"
                )
                deleted.append(value is True)
            except urllib.error.URLError:
                deleted.append(False)
        gates["sessions_deleted"] = deleted == [True, True]
        report["seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = all(gates.values())
    return report


def summarize(
    *,
    schema_rows: list[dict[str, Any]],
    isolation_rows: list[dict[str, Any]],
    cli_version: str,
    model: str,
    server_teardown_ok: bool,
) -> dict[str, Any]:
    supported = [
        row["model"]
        for row in schema_rows
        if row.get("gates", {}).get("provider_accepted_schema") is True
    ]
    session_ids = [
        str(row.get(key) or "")
        for row in isolation_rows
        for key in ("session_a", "session_b")
    ]
    schema_complete = bool(schema_rows) and all(
        row.get("gates", {}).get("session_created") is True
        and row.get("gates", {}).get("request_completed") is True
        and row.get("gates", {}).get("session_deleted") is True
        for row in schema_rows
    )
    isolation_complete = (
        len(isolation_rows) == 3
        and all(row.get("ok") is True for row in isolation_rows)
        and all(session_ids)
        and len(session_ids) == len(set(session_ids))
    )
    return {
        "schema_version": 1,
        "benchmark": "opencode_session_isolation",
        "cli_version": cli_version,
        "model": model,
        "contract": "schema_cross_model_and_session_override_isolation_3seed_v1",
        "schema_screen": schema_rows,
        "isolation_samples": isolation_rows,
        "json_schema_supported_models": supported,
        "gates": {
            "schema_screen_complete": schema_complete,
            "at_least_one_model_accepts_json_schema": bool(supported),
            "isolation_matrix_3seed": isolation_complete,
            "six_distinct_sessions": len(session_ids) == 6
            and len(session_ids) == len(set(session_ids)),
            "server_teardown_guaranteed": server_teardown_ok,
        },
        "production_activation_allowed": False,
        "decision": "retain_ephemeral_cli",
        "reason": (
            "La evaluación separa soporte JSON Schema de aislamiento. Incluso con tres "
            "semillas limpias, serve requiere contrato estructurado y supervisor productivo."
        ),
    }


def run_experiment(
    *,
    model: str = DEFAULT_MODEL,
    schema_models: tuple[str, ...] = DEFAULT_SCHEMA_MODELS,
) -> dict[str, Any]:
    executable = shutil.which("opencode.cmd") or shutil.which("opencode")
    if not executable:
        raise RuntimeError("OpenCode CLI is not installed")
    version_proc = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    version = (
        (version_proc.stdout or version_proc.stderr or "unknown")
        .strip()
        .splitlines()[0]
    )
    port = _free_port()
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(POLICY),
    }
    base_url = f"http://127.0.0.1:{port}"
    server = _start_server(executable, port, env)
    schema_rows: list[dict[str, Any]] = []
    isolation_rows: list[dict[str, Any]] = []
    try:
        if not _wait_for_port(port):
            raise RuntimeError("OpenCode readiness timeout for isolation benchmark")
        for schema_model in schema_models:
            schema_rows.append(run_schema_case(base_url, password, model=schema_model))
        forbidden_markers = {
            f"PUBLIC_{kind}_S{seed}"
            for seed in (1, 2, 3)
            for kind in ("OLD", "NEW", "FRESH")
        }
        for seed in (1, 2, 3):
            isolation_rows.append(
                run_isolation_seed(
                    base_url,
                    password,
                    model=model,
                    seed=seed,
                    forbidden_markers=forbidden_markers,
                )
            )
    finally:
        teardown_ok = _stop_server(server, port)
    return summarize(
        schema_rows=schema_rows,
        isolation_rows=isolation_rows,
        cli_version=version,
        model=model,
        server_teardown_ok=teardown_ok,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--schema-model", action="append", dest="schema_models")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    schema_models = tuple(args.schema_models or DEFAULT_SCHEMA_MODELS)
    report = run_experiment(model=args.model, schema_models=schema_models)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"gates": report["gates"], "decision": report["decision"]}))
    required = (
        "schema_screen_complete",
        "isolation_matrix_3seed",
        "six_distinct_sessions",
        "server_teardown_guaranteed",
    )
    return 0 if all(report["gates"][key] for key in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())

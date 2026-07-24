"""Probe exacto y fail-fast de un modelo catalog-only de OpenCode.

Ejecuta una sola inferencia sintética con el schema productivo ``submit_work``.
Un JSON textual válido no sustituye el campo structured del proveedor.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.work_contract import SUBMIT_WORK_SCHEMA  # noqa: E402
from scripts.benchmark_opencode_server_faults import (  # noqa: E402
    _extract_text,
    _request,
    _start_server,
    _stop_server,
)
from scripts.benchmark_opencode_session_isolation import (  # noqa: E402
    _error_summary,
    _model_ref,
)
from scripts.benchmark_opencode_transport import (  # noqa: E402
    POLICY,
    _free_port,
    _wait_for_port,
)


DEFAULT_MODEL = "opencode/ling-3.0-flash-free"


def classify_probe(
    *,
    model: str,
    cli_version: str,
    discovered_models: set[str],
    sample: dict[str, Any],
    server_teardown_ok: bool,
    observed_at: str,
) -> dict[str, Any]:
    gates = sample.get("gates") or {}
    session_lifecycle_complete = all(
        gates.get(name) is True
        for name in (
            "session_created",
            "request_completed",
            "session_deleted",
        )
    )
    schema_passed = gates.get("provider_accepted_submit_work_schema") is True
    provider_error = sample.get("provider_error") or {}
    structured_unsupported = (
        provider_error.get("name") == "StructuredOutputError"
    )
    if schema_passed:
        status = "eligible_for_role_classification"
        repeat_inference = True
    elif session_lifecycle_complete and structured_unsupported:
        status = "catalog_only_until_transport_or_model_change"
        repeat_inference = False
    else:
        status = "operational_diagnostic"
        repeat_inference = False
    checks = {
        "cli_version_observed": bool(cli_version),
        "exact_model_discovered": model in discovered_models,
        "single_inference": sample.get("inference_runs") == 1,
        "session_lifecycle_complete": session_lifecycle_complete,
        "server_teardown_guaranteed": server_teardown_ok,
    }
    provider_gate_conclusive = (
        checks["cli_version_observed"]
        and checks["exact_model_discovered"]
        and checks["single_inference"]
        and session_lifecycle_complete
        and (schema_passed or structured_unsupported)
    )
    return {
        "schema_version": 1,
        "benchmark": "opencode_catalog_model_probe",
        "observed_at": observed_at,
        "profile_id": "opencode_zen_free",
        "model": model,
        "cli_version": cli_version,
        "discovered_models": sorted(discovered_models),
        "contract": "production_submit_work_json_schema_single_seed_v1",
        "sample": sample,
        "checks": checks,
        "provider_gate_conclusive": provider_gate_conclusive,
        "probe_completed": all(checks.values()),
        "candidate_gate_passed": schema_passed and all(checks.values()),
        "decision": {
            "status": status,
            "repeat_inference": repeat_inference,
            "roles_granted": [],
            "automatic_selection_allowed": False,
            "quality_score_allowed": False,
        },
    }


def run_probe(*, model: str) -> dict[str, Any]:
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
    cli_version = (
        (version_proc.stdout or version_proc.stderr or "")
        .strip()
        .splitlines()[0]
    )
    models_proc = subprocess.run(
        [executable, "models", "opencode"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    discovered_models = {
        line.strip() for line in models_proc.stdout.splitlines() if line.strip()
    }
    port = _free_port()
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(POLICY),
    }
    base_url = f"http://127.0.0.1:{port}"
    server = _start_server(executable, port, env)
    expected = {
        "status": "completed",
        "summary": "LING_CATALOG_PROBE_OK",
        "ops": [],
    }
    session_id = ""
    sample: dict[str, Any] = {
        "seed": 1,
        "inference_runs": 0,
        "session_id": None,
        "finish": None,
        "text": None,
        "structured": None,
        "provider_error": None,
        "transport_error": None,
        "gates": {
            "session_created": False,
            "request_completed": False,
            "structured_submit_work_exact": False,
            "provider_accepted_submit_work_schema": False,
            "session_deleted": False,
        },
    }
    try:
        if not _wait_for_port(port):
            raise RuntimeError("OpenCode readiness timeout")
        _, session = _request(
            base_url,
            password,
            "POST",
            "/session",
            {"title": f"AI Teams catalog probe {model}"},
        )
        session_id = str(session.get("id") or "")
        sample["session_id"] = session_id or None
        sample["gates"]["session_created"] = bool(session_id)
        sample["inference_runs"] = 1
        _, response = _request(
            base_url,
            password,
            "POST",
            f"/session/{session_id}/message",
            {
                "model": _model_ref(model),
                "tools": {},
                "format": {"type": "json_schema", "schema": SUBMIT_WORK_SCHEMA},
                "parts": [
                    {
                        "type": "text",
                        "text": (
                            "Synthetic public probe. Return status completed, summary "
                            "LING_CATALOG_PROBE_OK and an empty ops array. Do not use tools."
                        ),
                    }
                ],
            },
            timeout=180,
        )
        info = response.get("info") or {}
        structured = info.get("structured")
        provider_error = _error_summary(info.get("error"))
        sample.update(
            {
                "finish": info.get("finish"),
                "text": _extract_text(response).strip() or None,
                "structured": structured,
                "provider_error": provider_error,
            }
        )
        sample["gates"].update(
            {
                "request_completed": info.get("finish") == "stop",
                "structured_submit_work_exact": structured == expected,
                "provider_accepted_submit_work_schema": (
                    provider_error is None and structured == expected
                ),
            }
        )
    except Exception as exc:  # receipt must preserve operational failures
        sample["transport_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if session_id:
            try:
                _, deleted = _request(
                    base_url, password, "DELETE", f"/session/{session_id}"
                )
                sample["gates"]["session_deleted"] = deleted is True
            except Exception:
                sample["gates"]["session_deleted"] = False
        teardown_ok = _stop_server(server, port)
    return classify_probe(
        model=model,
        cli_version=cli_version,
        discovered_models=discovered_models,
        sample=sample,
        server_teardown_ok=teardown_ok,
        observed_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reevaluate-from", type=Path)
    args = parser.parse_args()
    if args.reevaluate_from:
        source = json.loads(args.reevaluate_from.read_text(encoding="utf-8"))
        report = classify_probe(
            model=str(source["model"]),
            cli_version=str(source.get("cli_version") or ""),
            discovered_models={
                str(item) for item in source.get("discovered_models") or ()
            },
            sample=dict(source["sample"]),
            server_teardown_ok=(
                source.get("checks", {}).get("server_teardown_guaranteed")
                is True
            ),
            observed_at=str(source["observed_at"]),
        )
        report["reevaluation"] = {
            "reason": "separate_provider_gate_from_operational_cleanup",
            "provider_rerun": False,
        }
    else:
        report = run_probe(model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "probe_completed": report["probe_completed"],
                "candidate_gate_passed": report["candidate_gate_passed"],
                "decision": report["decision"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["provider_gate_conclusive"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

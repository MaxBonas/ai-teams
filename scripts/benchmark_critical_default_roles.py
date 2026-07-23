"""Canario reproducible de roles críticos para la cohorte M.7.1.

Cada muestra prueba un par exacto perfil+modelo+rol contra una familia de caso
congelada. El agregado solo considera completa una cohorte con tres semillas en
las dos familias; nunca extrapola ``lead`` a sus aliases.

Este script consume cuota real de suscripción cuando no se usa ``--aggregate-from``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from aiteam.adapters.work_contract import critical_fact_retention_instruction


REPO_ROOT = Path(__file__).resolve().parents[1]
ROLES = ("architect", "lead", "lead_executor", "quorum_auditor", "team_lead")
DEFAULT_MODELS = {
    "codex_subscription": "gpt-5.6-sol",
    "antigravity_subscription": "gemini-3.1-pro-high",
}

CASES: dict[str, dict[str, Any]] = {
    "tenant_queue_migration": {
        "facts": (
            "SQLite será la única fuente durable. Cada issue tiene tenant_id. El checkout actual "
            "separa SELECT y UPDATE y dos workers pueden ganar la misma issue. La migración no "
            "puede detener runs activas. Existe un flag para volver al camino anterior. El gate "
            "es ejecutar 100 reinicios con cero wakeups perdidos durante 24 horas."
        ),
        "anchors": {
            "durable_source": ("sqlite",),
            "tenant_boundary": ("tenant_id", "tenant"),
            "atomic_checkout": ("atomic", "atóm", "transaction", "transacci"),
            "rollback": ("rollback", "revert", "flag"),
            "restart_count": (r"\b100\b",),
            "observation_window": (r"\b24\s*(?:h|horas?|hours?)\b",),
            "durable_wakeup": ("wakeup",),
        },
        "forbidden": ("redis será", "redis will"),
    },
    "auth_rollout_incident": {
        "facts": (
            "El rollout de auth v2 está al 5 %. La tasa de error observada es 2,4 %, por encima "
            "del límite 1,0 % durante 10 minutos. Auth v1 sigue disponible para rollback. Release "
            "Engineer es owner. Reviewer acepta solo cuando logs y métricas confirmen recuperación. "
            "La rotación de secrets es una dependencia pendiente. La opción de desplegar el jueves "
            "fue descartada y no debe aparecer en la salida."
        ),
        "anchors": {
            "rollout_percent": (r"\b5\s*%",),
            "observed_error": (r"\b2[,.]4\s*%",),
            "error_limit": (r"\b1[,.]0\s*%",),
            "observation_window": (r"\b10\s*(?:min|minutes?|minutos?)\b",),
            "rollback_version": (r"\bauth\s+v1\b", r"\bv1\b"),
            "owner": ("release engineer",),
            "acceptor": ("reviewer",),
            "evidence_logs": ("logs",),
            "evidence_metrics": ("métric", "metric"),
            "secret_dependency": ("secret",),
        },
        "forbidden": ("desplegar el jueves", "deploy on thursday"),
    },
}

ROLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "architect": {
        "instruction": (
            "Define la decisión de arquitectura, límites e interfaces. Separa hechos de supuestos "
            "y especifica rollback y evidencia verificable; no asignes implementación genérica."
        ),
        "keys": ("decision", "constraints", "interfaces", "risks", "verification", "rollback"),
        "anchors": {
            "architecture_boundaries": ("boundary", "límite", "interface", "interfaz"),
            "explicit_assumptions": ("assumption", "supuesto"),
        },
    },
    "lead": {
        "instruction": (
            "Descompón el objetivo, asigna owners y aceptadores, dependencias, riesgos, gates y "
            "condiciones de escalado. Mantén accountability explícita."
        ),
        "keys": ("objective", "work_items", "risks", "verification", "escalation"),
        "anchors": {
            "explicit_owner": ("owner",),
            "explicit_acceptor": ("accepted_by", "acepta", "accept"),
        },
    },
    "lead_executor": {
        "instruction": (
            "Trabaja como Lead que ejecuta personalmente una tarea acotada. Propón una secuencia "
            "concreta de cambios y pruebas, sin fingir que ya ejecutaste herramientas o delegaciones."
        ),
        "keys": ("objective", "execution_steps", "evidence", "risks", "rollback", "escalation"),
        "anchors": {},
        "forbidden": ("tests passed", "pruebas pasaron", "delegué", "i delegated"),
    },
    "quorum_auditor": {
        "instruction": (
            "Audita adversarialmente la propuesta implícita en los hechos. Emite veredicto, "
            "contraejemplos, evidencia ausente, failure modes y recomendación go/no-go."
        ),
        "keys": ("verdict", "challenges", "missing_evidence", "failure_modes", "recommendation"),
        "anchors": {
            "go_no_go": (r"\bgo\b", r"\bno-go\b"),
            "adversarial_challenge": ("counterexample", "contraejemplo", "failure"),
        },
    },
    "team_lead": {
        "instruction": (
            "Coordina el equipo: objetivo, assignments, dependencias, aceptación, actualización "
            "de estado y escalado. Cada entrega debe tener owner y aceptador."
        ),
        "keys": ("objective", "assignments", "dependencies", "acceptance", "status_update", "escalation"),
        "anchors": {
            "explicit_owner": ("owner",),
            "explicit_acceptor": ("accepted_by", "acepta", "accept"),
        },
    },
}


def build_prompt(role: str, case_id: str, seed: int, prompt_version: str = "v1") -> str:
    contract = ROLE_CONTRACTS[role]
    keys = ", ".join(f'"{key}"' for key in contract["keys"])
    prompt = (
        "Responde exclusivamente con un objeto JSON válido, sin Markdown. "
        "No uses herramientas, no inventes ejecuciones y no añadas hechos externos.\n\n"
        f"ROL EXACTO: {role}\nSEMILLA DE REPETICIÓN: {seed}\n"
        f"OBLIGACIÓN: {contract['instruction']}\n"
        f"CASO CONGELADO ({case_id}): {CASES[case_id]['facts']}\n"
        f"El objeto debe contener exactamente estas claves de primer nivel: {keys}."
    )
    if prompt_version == "v2":
        prompt += critical_fact_retention_instruction()
    elif prompt_version != "v1":
        raise ValueError(f"unsupported prompt version: {prompt_version}")
    return prompt


def parse_json_output(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("JSON object not found")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("top-level output must be an object")
    return value


def evaluate_response(role: str, case_id: str, response: dict[str, Any]) -> dict[str, Any]:
    contract = ROLE_CONTRACTS[role]
    expected_keys = set(contract["keys"])
    actual_keys = set(response)
    normalized = json.dumps(response, ensure_ascii=False).lower()
    anchors = {
        **{
            f"case.{name}": alternatives
            for name, alternatives in CASES[case_id]["anchors"].items()
        },
        **{
            f"role.{name}": alternatives
            for name, alternatives in contract["anchors"].items()
        },
    }
    anchor_hits = {
        name: any(
            re.search(pattern, normalized, flags=re.IGNORECASE) is not None
            for pattern in alternatives
        )
        for name, alternatives in anchors.items()
    }
    forbidden = (*CASES[case_id].get("forbidden", ()), *contract.get("forbidden", ()))
    forbidden_hits = [
        pattern
        for pattern in forbidden
        if re.search(pattern, normalized, flags=re.IGNORECASE) is not None
    ]
    role_checks: dict[str, bool] = {}
    if role == "lead_executor":
        role_checks = {
            "execution_steps_nonempty": (
                isinstance(response.get("execution_steps"), list)
                and bool(response["execution_steps"])
            ),
            "evidence_nonempty": bool(response.get("evidence")),
        }
    return {
        "schema_exact": actual_keys == expected_keys,
        "missing_keys": sorted(expected_keys - actual_keys),
        "unexpected_keys": sorted(actual_keys - expected_keys),
        "anchors": anchor_hits,
        "anchors_retained": sum(anchor_hits.values()),
        "anchors_total": len(anchor_hits),
        "missing_anchors": [name for name, retained in anchor_hits.items() if not retained],
        "forbidden_hits": forbidden_hits,
        "role_checks": role_checks,
        "failed_role_checks": [
            name for name, passed in role_checks.items() if not passed
        ],
        "contract_passed": (
            actual_keys == expected_keys
            and all(anchor_hits.values())
            and all(role_checks.values())
            and not forbidden_hits
        ),
    }


def _usage_from_codex_jsonl(stdout: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = event.get("usage")
        if isinstance(candidate, dict):
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                if isinstance(candidate.get(key), int):
                    usage[key] = int(candidate[key])
    return usage


def _cli_version(executable: str) -> str:
    proc = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20, check=False,
    )
    return (proc.stdout or proc.stderr).strip()


def _run_codex(model: str, prompt: str, timeout: int) -> dict[str, Any]:
    executable = shutil.which("codex") or shutil.which("codex.cmd")
    if not executable:
        raise RuntimeError("codex executable not found")
    with tempfile.TemporaryDirectory(prefix="aiteam-critical-role-") as tmp:
        output = Path(tmp) / "last-message.json"
        command = [
            executable, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            "--ephemeral", "-c", "notify=[]", "-c", f'model="{model}"',
            "-c", 'model_reasoning_effort="medium"', "--json",
            "--output-last-message", str(output), "-",
        ]
        proc = subprocess.run(
            command, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=tmp, timeout=timeout, check=False,
        )
        raw = output.read_text(encoding="utf-8") if output.is_file() else ""
    return {
        "returncode": proc.returncode,
        "raw": raw,
        "stderr": proc.stderr[-2000:],
        "usage": _usage_from_codex_jsonl(proc.stdout),
        "cli_version": _cli_version(executable),
    }


def _run_antigravity(model: str, prompt: str, timeout: int) -> dict[str, Any]:
    executable = shutil.which("agy") or shutil.which("agy.exe")
    if not executable:
        raise RuntimeError("agy executable not found")
    with tempfile.TemporaryDirectory(prefix="aiteam-critical-role-") as tmp:
        proc = subprocess.run(
            [
                executable, "--new-project", "--print", prompt,
                "--print-timeout", f"{timeout}s", "--model", model,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=tmp, timeout=timeout + 15, check=False,
        )
    return {
        "returncode": proc.returncode,
        "raw": proc.stdout,
        "stderr": proc.stderr[-2000:],
        "usage": {},
        "cli_version": _cli_version(executable),
    }


def run_sample(
    *, profile_id: str, model: str, role: str, case_id: str, seed: int, timeout: int,
    prompt_version: str = "v1",
) -> dict[str, Any]:
    prompt = build_prompt(role, case_id, seed, prompt_version)
    started = time.monotonic()
    try:
        execution = (
            _run_codex(model, prompt, timeout)
            if profile_id == "codex_subscription"
            else _run_antigravity(model, prompt, timeout)
        )
    except subprocess.TimeoutExpired as exc:
        execution = {
            "returncode": None, "raw": "", "stderr": str(exc), "usage": {},
            "cli_version": None,
        }
        response = {}
        evaluation = {"contract_passed": False, "error": "TimeoutExpired"}
        status = "failed"
    else:
        response = {}
        parse_error: str | None = None
        if execution["returncode"] == 0:
            try:
                response = parse_json_output(execution["raw"])
            except ValueError as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
        evaluation = evaluate_response(role, case_id, response) if response else {
            "contract_passed": False,
            "error": parse_error or "missing_valid_response",
        }
        status = (
            "completed"
            if execution["returncode"] == 0 and parse_error is None
            else "failed"
        )
    return {
        "schema_version": 1,
        "benchmark": "critical_default_role_canary",
        "profile_id": profile_id,
        "channel": "subscription",
        "cli_version": execution["cli_version"],
        "model": model,
        "role": role,
        "case_id": case_id,
        "seed": seed,
        "prompt_version": prompt_version,
        "status": status,
        "wall_seconds": round(time.monotonic() - started, 3),
        "usage": execution["usage"],
        "response": response,
        "raw_output_on_failure": execution["raw"] if status == "failed" else None,
        "evaluation": evaluation,
        "diagnostic": execution["stderr"],
        "harness": {
            "workspace": "isolated_temporary_directory",
            "tools": "prompt_prohibited_not_transport_enforced",
            "judge": "deterministic_hidden_rubric",
        },
        "ok": status == "completed" and bool(evaluation["contract_passed"]),
    }


def _canonical_response_hash(response: Any) -> str:
    payload = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    identities = {
        (report.get("profile_id"), report.get("model"), report.get("role"))
        for report in reports
    }
    expected_samples = {(case_id, seed) for case_id in CASES for seed in (1, 2, 3)}
    observed_samples = {(report.get("case_id"), report.get("seed")) for report in reports}
    prompt_versions = {str(report.get("prompt_version") or "v1") for report in reports}
    exact_pair = len(identities) == 1
    complete = (
        exact_pair
        and len(prompt_versions) == 1
        and observed_samples == expected_samples
        and len(reports) == len(expected_samples)
    )
    passed = sum(bool(report.get("ok")) for report in reports)
    seconds = [float(report["wall_seconds"]) for report in reports if "wall_seconds" in report]
    profile_id, model, role = next(iter(identities)) if exact_pair else (None, None, None)
    manifest = sorted(
        [
            {
                "receipt": report.get("_source_receipt"),
                "case_id": report.get("case_id"),
                "seed": report.get("seed"),
                "ok": report.get("ok") is True,
                "response_sha256": _canonical_response_hash(report.get("response")),
            }
            for report in reports
        ],
        key=lambda item: (str(item["case_id"]), int(item["seed"] or 0)),
    )
    source_receipts = [str(item["receipt"]) for item in manifest if item["receipt"]]
    sources_bound = (
        len(source_receipts) == len(expected_samples)
        and len(set(source_receipts)) == len(expected_samples)
    )
    responses_hashed = all(item["response_sha256"] for item in manifest)
    calibrated = (
        complete
        and sources_bound
        and responses_hashed
        and passed == len(expected_samples)
    )
    return {
        "schema_version": 1,
        "benchmark": "critical_default_role_canary_aggregate",
        "profile_id": profile_id,
        "model": model,
        "role": role,
        "prompt_version": next(iter(prompt_versions)) if len(prompt_versions) == 1 else None,
        "required_cases": list(CASES),
        "required_seeds": [1, 2, 3],
        "samples_observed": len(reports),
        "samples_passed": passed,
        "matrix_complete": complete,
        "source_receipts": source_receipts,
        "sample_manifest": manifest,
        "integrity": {
            "sources_bound": sources_bound,
            "responses_hashed": responses_hashed,
            "single_prompt_version": len(prompt_versions) == 1,
        },
        "wall_seconds_median": round(statistics.median(seconds), 3) if seconds else None,
        "conclusion": {
            "exact_pair_calibrated": calibrated,
            "default_change_allowed": False,
            "reason": (
                "eligible_for_registry_review"
                if calibrated
                else "incomplete_or_failed_exact_pair"
            ),
            "next_gate": "M.7.4 snapshot vivo y promoción gobernada",
        },
    }


def compare_prompt_versions(
    v1_reports: list[dict[str, Any]], v2_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare one weak role/case family without treating it as calibration."""
    combined = [*v1_reports, *v2_reports]
    identities = {
        (
            report.get("profile_id"),
            report.get("model"),
            report.get("role"),
            report.get("case_id"),
        )
        for report in combined
    }
    expected_seeds = {1, 2, 3}

    def version_valid(reports: list[dict[str, Any]], expected: str) -> bool:
        return (
            len(reports) == 3
            and {report.get("seed") for report in reports} == expected_seeds
            and {str(report.get("prompt_version") or "v1") for report in reports}
            == {expected}
        )

    comparable = (
        len(identities) == 1
        and version_valid(v1_reports, "v1")
        and version_valid(v2_reports, "v2")
    )
    profile_id, model, role, case_id = (
        next(iter(identities)) if len(identities) == 1 else (None, None, None, None)
    )
    v1_passed = sum(report.get("ok") is True for report in v1_reports)
    v2_passed = sum(report.get("ok") is True for report in v2_reports)
    return {
        "schema_version": 1,
        "benchmark": "critical_default_role_prompt_comparison",
        "profile_id": profile_id,
        "model": model,
        "role": role,
        "case_id": case_id,
        "required_seeds": [1, 2, 3],
        "comparable": comparable,
        "v1_samples_passed": v1_passed,
        "v2_samples_passed": v2_passed,
        "pass_delta": v2_passed - v1_passed if comparable else None,
        "improvement_observed": comparable and v2_passed > v1_passed,
        "regression_observed": comparable and v2_passed < v1_passed,
        "source_receipts": {
            "v1": [report.get("_source_receipt") for report in v1_reports],
            "v2": [report.get("_source_receipt") for report in v2_reports],
        },
        "conclusion": {
            "calibration_allowed": False,
            "next_gate": "rerun_full_two_case_v2_matrix_before_registry_change",
        },
    }


def reevaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    updated = dict(report)
    harness = dict(report.get("harness") or {})
    harness["tools"] = "prompt_prohibited_not_transport_enforced"
    updated["harness"] = harness
    response = report.get("response")
    if not isinstance(response, dict) or not response:
        return updated
    evaluation = evaluate_response(
        str(report["role"]), str(report["case_id"]), response,
    )
    updated["evaluation"] = evaluation
    updated["ok"] = report.get("status") == "completed" and evaluation["contract_passed"]
    updated["reevaluation"] = {
        "provider_rerun": False,
        "reason": "deterministic_evaluator_contract_update",
    }
    return updated


def _relative_receipt(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(DEFAULT_MODELS))
    parser.add_argument("--model")
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--case", dest="case_id", choices=tuple(CASES))
    parser.add_argument("--seed", type=int, choices=(1, 2, 3))
    parser.add_argument("--prompt-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--aggregate-from", type=Path, nargs="+")
    parser.add_argument("--compare-v1-from", type=Path, nargs="+")
    parser.add_argument("--compare-v2-from", type=Path, nargs="+")
    parser.add_argument("--reevaluate-from", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.compare_v1_from) != bool(args.compare_v2_from):
        parser.error("--compare-v1-from and --compare-v2-from must be used together")
    if args.compare_v1_from:
        versions = []
        for paths in (args.compare_v1_from, args.compare_v2_from):
            reports = []
            for path in paths:
                source = json.loads(path.read_text(encoding="utf-8"))
                source["_source_receipt"] = _relative_receipt(path)
                reports.append(source)
            versions.append(reports)
        report = compare_prompt_versions(*versions)
        ok = bool(report["comparable"])
    elif args.aggregate_from:
        source_reports = []
        for path in args.aggregate_from:
            source = json.loads(path.read_text(encoding="utf-8"))
            source["_source_receipt"] = _relative_receipt(path)
            source_reports.append(source)
        report = aggregate_reports(source_reports)
        ok = bool(report["conclusion"]["exact_pair_calibrated"])
    elif args.reevaluate_from:
        report = reevaluate_report(
            json.loads(args.reevaluate_from.read_text(encoding="utf-8"))
        )
        ok = bool(report["ok"])
    else:
        if not all((args.profile, args.role, args.case_id, args.seed)):
            parser.error("--profile, --role, --case and --seed are required for a live sample")
        model = args.model or DEFAULT_MODELS[args.profile]
        report = run_sample(
            profile_id=args.profile, model=model, role=args.role, case_id=args.case_id,
            seed=args.seed, timeout=max(30, args.timeout),
            prompt_version=args.prompt_version,
        )
        ok = bool(report["ok"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "benchmark": report["benchmark"]}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

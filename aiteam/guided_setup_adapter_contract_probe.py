"""Probe efímero y exacto de structured output para un adapter configurado."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from aiteam.adapters.registry import AdapterRegistry, build_default_registry
from aiteam.user_config import (
    inject_adapter_secrets,
    load_adapter_profiles,
    resolve_adapter_config,
)

SCHEMA_VERSION = "guided_setup_adapter_contract_probe_receipt_v1"
MARKER = "AITEAM_PREFLIGHT_CONTRACT_OK"
SecretInjector = Callable[
    [dict[str, str], str, dict[str, Any]],
    dict[str, str],
]


def run_exact_adapter_contract_probe(
    profile_id: str,
    model_id: str,
    timeout_seconds: int,
    *,
    consent_granted: bool,
    quota_acknowledged: bool,
    profiles: list[dict[str, Any]] | None = None,
    registry: AdapterRegistry | None = None,
    secret_injector: SecretInjector = inject_adapter_secrets,
) -> dict[str, Any]:
    """Run one exact submit_work probe without persisting health or catalog."""
    if consent_granted is not True:
        raise ValueError("guided_setup_adapter_probe_consent_required")
    if quota_acknowledged is not True:
        raise ValueError("guided_setup_adapter_probe_quota_ack_required")
    if not 1 <= timeout_seconds <= 180:
        raise ValueError("guided_setup_adapter_probe_timeout_invalid")
    clean_profile_id = str(profile_id or "").strip()
    clean_model_id = str(model_id or "").strip()
    profile = next(
        (
            row
            for row in (profiles if profiles is not None else load_adapter_profiles())
            if str(row.get("id") or "") == clean_profile_id
        ),
        None,
    )
    if profile is None:
        raise ValueError("guided_setup_adapter_probe_profile_missing")
    options = [
        row
        for row in profile.get("model_options") or ()
        if isinstance(row, Mapping)
    ]
    model = next(
        (
            row
            for row in options
            if str(row.get("value") or "") == clean_model_id
        ),
        None,
    )
    if model is None:
        raise ValueError("guided_setup_adapter_probe_model_missing")
    structured_output = str(
        model.get("structured_output")
        or profile.get("structured_output")
        or ""
    )
    if structured_output not in {"json_object", "json_schema"}:
        raise ValueError(
            "guided_setup_adapter_probe_structured_output_unsupported"
        )
    adapter_type = str(profile.get("adapter_type") or "").strip()
    runtime = (registry or build_default_registry()).get(adapter_type)
    if runtime is None:
        raise ValueError("guided_setup_adapter_probe_runtime_missing")
    config = resolve_adapter_config(
        adapter_type,
        {
            "profile_id": clean_profile_id,
            "model": clean_model_id,
            "timeout_sec": timeout_seconds,
            "sandbox": "read-only",
            "permission_mode": "plan",
            "approval_policy": "never",
        },
    )
    with_config = getattr(runtime, "with_config", None)
    if not callable(with_config):
        raise TypeError(
            "guided_setup_adapter_probe_timeout_not_configurable"
        )
    runtime = with_config(config)

    with TemporaryDirectory(prefix="aiteam-adapter-probe-") as temporary:
        empty_workspace = Path(temporary).resolve()
        config["cwd"] = str(empty_workspace)
        runtime = with_config(config)
        env = runtime.build_env(
            run_id="guided-setup-adapter-contract-probe",
            wake_context={
                "issue_id": "",
                "reason": "guided_setup_adapter_contract_probe",
                "agent_role": "worker",
                "agent_skill": (
                    "Eres un probe de contrato sin herramientas. Devuelve "
                    "submit_work con ops=[], status=completed y summary "
                    f'exactamente "{MARKER}". No hagas ninguna otra acción.'
                ),
                "wake_payload_json": (
                    '{"contract":"guided_setup_adapter_contract_probe_v1",'
                    '"operations_allowed":[]}'
                ),
            },
        )
        env = {
            **env,
            "AITEAM_MODEL": clean_model_id,
            "AITEAM_OPENAI_MODEL": clean_model_id,
            "AITEAM_GEMINI_MODEL": clean_model_id,
            "AITEAM_WORKSPACE_ROOT": str(empty_workspace),
        }
        env = secret_injector(env, adapter_type, config)
        result = runtime.execute(
            {
                "id": "guided-setup-adapter-contract-probe",
                "issue_id": "",
            },
            env,
        )

    actions = result.actions if isinstance(result.actions, dict) else {}
    passed = (
        result.status == "completed"
        and not actions
        and str(result.output or "").strip() == MARKER
    )
    usage = _numeric_usage(result.usage)
    quota_observed = any(
        value > 0
        for key, value in usage.items()
        if "token" in key.lower()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": clean_profile_id,
        "model_id": clean_model_id,
        "adapter_type": adapter_type,
        "contract": "submit_work_empty_ops_v1",
        "status": "passed" if passed else "failed",
        "reason": (
            "exact_structured_output_passed"
            if passed
            else str(result.error_code or "contract_marker_mismatch")
        ),
        "attempts": 1,
        "timeout_seconds": timeout_seconds,
        "usage": usage,
        "quota": {
            "possible": True,
            "token_usage_observed": quota_observed,
            "actual_cost_cents": max(
                0,
                int(result.actual_cost_cents or 0),
            ),
        },
        "scope": {
            "remote_call_attempted": True,
            "credential_may_be_used": True,
            "secret_value_emitted": False,
            "output_emitted": False,
            "workspace_mode": "isolated_empty_read_only",
            "workspace_mutated": False,
            "health_or_catalog_persisted": False,
            "defaults_mutated": False,
            "automatic_install": False,
        },
    }


def _numeric_usage(value: Any) -> dict[str, int | float]:
    if value is None:
        return {}
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key)[:100]: number
        for key, number in list(value.items())[:30]
        if isinstance(number, (int, float)) and not isinstance(number, bool)
    }

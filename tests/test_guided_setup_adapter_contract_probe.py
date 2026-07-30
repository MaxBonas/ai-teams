from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from aiteam.adapters.registry import (
    AdapterDescriptor,
    AdapterRegistry,
    ExecutionResult,
)
from aiteam.guided_setup_adapter_contract_probe import (
    MARKER,
    run_exact_adapter_contract_probe,
)


@dataclass
class FakeRuntime:
    descriptor: AdapterDescriptor
    timeout: int = 999
    model: str = ""
    observed: list[dict[str, Any]] | None = None
    result: ExecutionResult | None = None

    def with_config(self, config: dict[str, Any]) -> FakeRuntime:
        return replace(
            self,
            timeout=int(config["timeout_sec"]),
            model=str(config["model"]),
        )

    def build_env(
        self,
        *,
        run_id: str,
        wake_context: dict[str, object],
    ) -> dict[str, str]:
        assert run_id == "guided-setup-adapter-contract-probe"
        assert MARKER in str(wake_context["agent_skill"])
        return {}

    def execute(
        self,
        run: dict[str, Any],
        env: dict[str, str],
    ) -> ExecutionResult:
        assert self.observed is not None
        self.observed.append(
            {
                "run": run,
                "env": env,
                "timeout": self.timeout,
                "model": self.model,
            }
        )
        return self.result or ExecutionResult(
            status="completed",
            output=MARKER,
            usage={"input_tokens": 10, "output_tokens": 4},
            actual_cost_cents=2,
            actions={},
        )


def _profile(*, structured_output: str = "json_schema") -> dict[str, Any]:
    return {
        "id": "fixture_profile",
        "adapter_type": "fixture_adapter",
        "channel": "api",
        "structured_output": structured_output,
        "config": {"api_key_ref": "secret:fixture:default"},
        "model_options": [
            {
                "value": "fixture-model",
                "tier": "standard",
                "structured_output": structured_output,
            }
        ],
    }


def _registry(
    observed: list[dict[str, Any]],
    *,
    result: ExecutionResult | None = None,
) -> AdapterRegistry:
    return AdapterRegistry(
        [
            FakeRuntime(
                AdapterDescriptor(
                    adapter_type="fixture_adapter",
                    channel="api",
                ),
                observed=observed,
                result=result,
            )
        ]
    )


def _run(
    *,
    profiles: list[dict[str, Any]] | None = None,
    registry: AdapterRegistry | None = None,
    secret_injector=lambda env, _adapter, _config: {
        **env,
        "FIXTURE_KEY": "never-emitted",
    },
) -> dict[str, Any]:
    return run_exact_adapter_contract_probe(
        "fixture_profile",
        "fixture-model",
        45,
        consent_granted=True,
        quota_acknowledged=True,
        profiles=profiles or [_profile()],
        registry=registry or _registry([]),
        secret_injector=secret_injector,
    )


def test_exact_probe_is_bounded_redacted_and_reports_quota() -> None:
    observed: list[dict[str, Any]] = []
    receipt = _run(registry=_registry(observed))

    assert len(observed) == 1
    assert observed[0]["timeout"] == 45
    assert observed[0]["model"] == "fixture-model"
    assert observed[0]["env"]["AITEAM_MODEL"] == "fixture-model"
    assert receipt["status"] == "passed"
    assert receipt["usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert receipt["quota"] == {
        "possible": True,
        "token_usage_observed": True,
        "actual_cost_cents": 2,
    }
    assert "never-emitted" not in str(receipt)
    assert receipt["scope"]["health_or_catalog_persisted"] is False
    assert receipt["scope"]["automatic_install"] is False


@pytest.mark.parametrize(
    ("consent", "quota", "code"),
    [
        (False, True, "consent_required"),
        (True, False, "quota_ack_required"),
    ],
)
def test_consent_is_required_before_secret_injection(
    consent: bool,
    quota: bool,
    code: str,
) -> None:
    injected = []
    with pytest.raises(ValueError, match=code):
        run_exact_adapter_contract_probe(
            "fixture_profile",
            "fixture-model",
            45,
            consent_granted=consent,
            quota_acknowledged=quota,
            profiles=[_profile()],
            registry=_registry([]),
            secret_injector=lambda *_args: injected.append(True),
        )

    assert injected == []


def test_profile_model_and_structured_contract_fail_before_secret_read() -> None:
    injected = []
    for profile_id, model_id, profiles, code in (
        ("missing", "fixture-model", [_profile()], "profile_missing"),
        ("fixture_profile", "missing", [_profile()], "model_missing"),
        (
            "fixture_profile",
            "fixture-model",
            [_profile(structured_output="none")],
            "structured_output_unsupported",
        ),
    ):
        with pytest.raises(ValueError, match=code):
            run_exact_adapter_contract_probe(
                profile_id,
                model_id,
                45,
                consent_granted=True,
                quota_acknowledged=True,
                profiles=profiles,
                registry=_registry([]),
                secret_injector=lambda *_args: injected.append(True),
            )

    assert injected == []


def test_probe_rejects_marker_or_operations_without_emitting_output() -> None:
    result = ExecutionResult(
        status="completed",
        output="wrong marker with secret",
        error="private provider response",
        error_code="tool_parse_error",
        usage={"input_tokens": 5, "provider_request_id": "private"},
        actions={"ops": [{"type": "write_file"}]},
    )
    receipt = _run(registry=_registry([], result=result))

    assert receipt["status"] == "failed"
    assert receipt["reason"] == "tool_parse_error"
    assert receipt["usage"] == {"input_tokens": 5}
    assert "wrong marker" not in str(receipt)
    assert "private" not in str(receipt)


def test_runtime_without_timeout_configuration_fails_before_secret_read() -> None:
    class NoConfigRuntime:
        descriptor = AdapterDescriptor(
            adapter_type="fixture_adapter",
            channel="api",
        )

    injected = []
    with pytest.raises(TypeError, match="timeout_not_configurable"):
        _run(
            registry=AdapterRegistry([NoConfigRuntime()]),
            secret_injector=lambda *_args: injected.append(True),
        )

    assert injected == []

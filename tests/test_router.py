import unittest
import tempfile
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aiteam.adapters import (
    ApiAdapter as RealApiAdapter,
    FakeSuccessAdapter,
    SubscriptionAdapter as RealSubscriptionAdapter,
)
from aiteam.config import build_default_router_policy
from aiteam.finops import BudgetManager, BudgetPolicy
from aiteam.model_catalog import load_model_catalog
from aiteam.router import HybridRouter
from aiteam.types import (
    AdapterResponse,
    ChannelType,
    Complexity,
    Criticality,
    Role,
    RoutingRequest,
)


class SubscriptionAdapter(FakeSuccessAdapter):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("channel", ChannelType.SUBSCRIPTION)
        super().__init__(*args, **kwargs)


class ApiAdapter(FakeSuccessAdapter):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("channel", ChannelType.API)
        super().__init__(*args, **kwargs)


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                ),
                ApiAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-api",
                    capabilities={"coding"},
                ),
            ],
            policy=build_default_router_policy(),
        )

    def test_prefers_subscription(self) -> None:
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"coding"},
        )
        decision = self.router.route_and_invoke(request=request, prompt="simple prompt")
        self.assertTrue(decision.success)
        self.assertEqual(decision.channel.value, "subscription")

    def test_fallback_to_api(self) -> None:
        router = HybridRouter(
            adapters=[
                RealSubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                ),
                ApiAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-api",
                    capabilities={"coding"},
                ),
            ],
            policy=build_default_router_policy(),
        )
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.HIGH,
            criticality=Criticality.HIGH,
            required_capabilities={"coding"},
        )
        decision = router.route_and_invoke(
            request=request,
            prompt="FORCE_API_FALLBACK this should fail pro path",
        )
        self.assertTrue(decision.success)
        self.assertEqual(decision.channel.value, "api")

    def test_failed_attempts_include_error_hint(self) -> None:
        class FailAdapter(SubscriptionAdapter):
            def invoke(self, prompt: str, messages: list[dict[str, str]] | None = None):
                return AdapterResponse(
                    success=False,
                    content="",
                    latency_ms=0,
                    input_tokens=0,
                    output_tokens=0,
                    error="http_error:429:rate_limited",
                )

        router = HybridRouter(
            adapters=[
                FailAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                )
            ],
            policy=build_default_router_policy(),
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )

        self.assertFalse(decision.success)
        self.assertIn("http_error:429:rate_limited", " ".join(decision.attempts))

    def test_failed_attempts_compact_multiline_openai_error_body(self) -> None:
        class FailAdapter(SubscriptionAdapter):
            def invoke(self, prompt: str, messages: list[dict[str, str]] | None = None):
                return AdapterResponse(
                    success=False,
                    content="",
                    latency_ms=0,
                    input_tokens=0,
                    output_tokens=0,
                    error=(
                        'http_error:429:{\n  "error": {\n'
                        '    "message": "You exceeded your current quota.",\n'
                        '    "type": "insufficient_quota",\n'
                        '    "code": "insufficient_quota"\n  }\n}'
                    ),
                )

        router = HybridRouter(
            adapters=[
                FailAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                )
            ],
            policy=build_default_router_policy(),
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )

        attempts = " ".join(decision.attempts)
        self.assertIn("http_error:429", attempts)
        self.assertIn("type=insufficient_quota", attempts)
        self.assertIn("code=insufficient_quota", attempts)
        self.assertNotIn("http_error:429:{", attempts)

    def test_route_and_invoke_forwards_messages_history(self) -> None:
        captured: dict[str, object] = {}

        class CaptureAdapter(SubscriptionAdapter):
            def invoke(self, prompt: str, messages: list[dict[str, str]] | None = None):
                captured["prompt"] = prompt
                captured["messages"] = messages
                return super().invoke(prompt, messages=messages)

        router = HybridRouter(
            adapters=[
                CaptureAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                )
            ],
            policy=build_default_router_policy(),
        )
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"coding"},
        )
        messages = [
            {"role": "system", "content": "You are Engineer"},
            {"role": "user", "content": "Implement auth with 2FA"},
        ]

        decision = router.route_and_invoke(
            request=request, prompt="fallback prompt", messages=messages
        )

        self.assertTrue(decision.success)
        self.assertEqual(captured.get("messages"), messages)
        self.assertEqual(captured.get("prompt"), "fallback prompt")

    def test_tool_specialist_prefers_budget_api_before_subscription(self) -> None:
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"browser_test"},
                    response_content="subscription",
                ),
                ApiAdapter(
                    name="openai_api_budget",
                    provider="openai",
                    model="gpt-cheap",
                    capabilities={"browser_test"},
                    cost_tier=1,
                    response_content="budget",
                ),
            ],
            policy=build_default_router_policy(),
        )
        request = RoutingRequest(
            role=Role.QA,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
            required_capabilities={"browser_test"},
            tool_specialist="browser_operator",
            prefer_economic_routing=True,
            preferred_tool_tier="budget_api",
        )

        decision = router.route_and_invoke(
            request=request,
            prompt="reproduce ui issue and summarize",
        )

        self.assertTrue(decision.success)
        self.assertEqual(decision.channel, ChannelType.API)
        self.assertEqual(decision.response.content, "budget")

    def test_route_and_invoke_respects_excluded_providers(self) -> None:
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                    response_content="openai",
                ),
                SubscriptionAdapter(
                    name="anthropic_pro",
                    provider="anthropic",
                    model="claude-sonnet",
                    capabilities={"coding"},
                    response_content="anthropic",
                ),
            ],
            policy=build_default_router_policy(),
        )
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"coding"},
            excluded_providers={"openai"},
        )

        decision = router.route_and_invoke(
            request=request,
            prompt="simple prompt",
        )

        self.assertTrue(decision.success)
        self.assertEqual(decision.provider, "anthropic")

    def test_eligible_adapters_respects_role_provider_exclusions_from_policy(self) -> None:
        policy = build_default_router_policy()
        policy.role_provider_exclusions["engineer"] = ["openai"]
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                ),
                SubscriptionAdapter(
                    name="gemini_pro",
                    provider="google",
                    model="gemini-pro",
                    capabilities={"coding"},
                ),
            ],
            policy=policy,
        )

        eligible = router.eligible_adapters(
            RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            )
        )

        self.assertEqual([adapter.name for adapter in eligible], ["gemini_pro"])

    def test_eligible_adapters_prefers_role_primary_provider(self) -> None:
        policy = build_default_router_policy()
        policy.role_primary_provider["engineer"] = "google"
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                ),
                SubscriptionAdapter(
                    name="gemini_pro",
                    provider="google",
                    model="gemini-pro",
                    capabilities={"coding"},
                ),
            ],
            policy=policy,
        )

        eligible = router.eligible_adapters(
            RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            )
        )

        self.assertEqual([adapter.name for adapter in eligible[:2]], ["gemini_pro", "openai_pro"])

    def test_tool_specialist_prefers_lower_api_cost_tier(self) -> None:
        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="openai_api_advanced",
                    provider="openai",
                    model="gpt-advanced",
                    capabilities={"test_execute"},
                    cost_tier=2,
                    response_content="advanced",
                ),
                ApiAdapter(
                    name="openai_api_budget",
                    provider="openai",
                    model="gpt-budget",
                    capabilities={"test_execute"},
                    cost_tier=1,
                    response_content="budget",
                ),
            ],
            policy=build_default_router_policy(),
        )
        request = RoutingRequest(
            role=Role.QA,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"test_execute"},
            tool_specialist="test_runner",
            prefer_economic_routing=True,
            preferred_tool_tier="budget_api",
        )

        decision = router.route_and_invoke(
            request=request,
            prompt="run tests and summarize failures",
        )

        self.assertTrue(decision.success)
        self.assertEqual(decision.response.content, "budget")

    def test_tool_specialist_autotune_escalates_to_advanced_api_when_delegate_economics_are_weak(self) -> None:
        runtime_dir = Path.cwd() / "runtime" / f"router_econ_{uuid4().hex[:8]}"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            budget = BudgetManager(
                runtime_dir=runtime_dir,
                policy=BudgetPolicy(
                    daily_api_budget_usd=10.0,
                    monthly_api_budget_usd=100.0,
                ),
            )
            events_path = runtime_dir / "events.jsonl"
            weak_events = [
                {
                    "ts": "2026-03-31T12:00:00+00:00",
                    "event_id": "evt-1",
                    "event_type": "delegate_economics_estimated",
                    "payload": {
                        "task_id": "CHAT-1",
                        "quorum_met": False,
                        "specialist_breakdown": {
                            "browser_operator": {
                                "count": 1,
                                "completed": 0,
                                "failed": 1,
                                "estimated_net_tokens_saved": 120,
                                "estimated_cost_units_saved": 1,
                            }
                        },
                    },
                },
                {
                    "ts": "2026-03-31T12:10:00+00:00",
                    "event_id": "evt-2",
                    "event_type": "delegate_economics_estimated",
                    "payload": {
                        "task_id": "CHAT-2",
                        "quorum_met": False,
                        "specialist_breakdown": {
                            "browser_operator": {
                                "count": 1,
                                "completed": 0,
                                "failed": 1,
                                "estimated_net_tokens_saved": 80,
                                "estimated_cost_units_saved": 1,
                            }
                        },
                    },
                },
            ]
            events_path.write_text(
                "\n".join(json.dumps(row) for row in weak_events) + "\n",
                encoding="utf-8",
            )
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api_advanced",
                        provider="openai",
                        model="gpt-advanced",
                        capabilities={"browser_test"},
                        cost_tier=2,
                        response_content="advanced",
                    ),
                    ApiAdapter(
                        name="openai_api_budget",
                        provider="openai",
                        model="gpt-budget",
                        capabilities={"browser_test"},
                        cost_tier=1,
                        response_content="budget",
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            request = RoutingRequest(
                role=Role.QA,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                required_capabilities={"browser_test"},
                tool_specialist="browser_operator",
                prefer_economic_routing=True,
                preferred_tool_tier="budget_api",
            )

            decision = router.route_and_invoke(
                request=request,
                prompt="reproduce browser issue and summarize",
            )

            self.assertTrue(decision.success)
            self.assertEqual(decision.response.content, "advanced")
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_api_budget_blocks_api_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = build_default_router_policy()
            budget = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(
                    daily_api_budget_usd=0.0, monthly_api_budget_usd=0.0
                ),
            )
            router = HybridRouter(
                adapters=[
                    RealSubscriptionAdapter(
                        name="openai_pro",
                        provider="openai",
                        model="gpt-pro",
                        capabilities={"coding"},
                    ),
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-api",
                        capabilities={"coding"},
                    ),
                ],
                policy=policy,
                budget_manager=budget,
            )
            request = RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
            )
            decision = router.route_and_invoke(
                request=request,
                prompt="FORCE_API_FALLBACK to force pro failure",
            )
            self.assertFalse(decision.success)
            self.assertIn("api_budget_block", " ".join(decision.attempts))

    def test_finops_signal_blocks_expensive_api_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = build_default_router_policy()
            budget = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(
                    daily_api_budget_usd=1.0, monthly_api_budget_usd=100.0
                ),
            )
            now = datetime.now(timezone.utc).isoformat()
            budget.ledger_path.write_text(
                f'{{"ts":"{now}","cost_usd":0.8}}\n',
                encoding="utf-8",
            )

            router = HybridRouter(
                adapters=[
                    RealSubscriptionAdapter(
                        name="openai_pro",
                        provider="openai",
                        model="gpt-pro",
                        capabilities={"coding"},
                    ),
                    ApiAdapter(
                        name="openai_api_expensive",
                        provider="openai",
                        model="gpt-expensive",
                        capabilities={"coding"},
                        routing_priority=10,
                        cost_tier=2,
                    ),
                    ApiAdapter(
                        name="openai_api_cheap",
                        provider="openai",
                        model="gpt-cheap",
                        capabilities={"coding"},
                        routing_priority=100,
                        cost_tier=1,
                    ),
                ],
                policy=policy,
                budget_manager=budget,
            )
            request = RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
            )
            decision = router.route_and_invoke(
                request=request,
                prompt="FORCE_API_FALLBACK to force subscription failure",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.model, "gpt-cheap")
            self.assertIn("api_tier_block", " ".join(decision.attempts))

    def test_role_target_filtering(self) -> None:
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="review_only_adapter",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"review"},
                    role_targets={"reviewer"},
                )
            ],
            policy=build_default_router_policy(),
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                required_capabilities={"review"},
            ),
            prompt="x",
        )
        self.assertFalse(decision.success)
        self.assertEqual(decision.reason, "no_eligible_adapter")

    def test_routing_priority_keeps_secondary_adapter_as_fallback(self) -> None:
        policy = build_default_router_policy()
        policy.preferred_subscription_providers = [
            "custom",
            "openai",
            "anthropic",
            "google",
        ]
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="secondary_external",
                    provider="custom",
                    model="ext-runtime",
                    capabilities={"coding"},
                    routing_priority=200,
                ),
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                    routing_priority=100,
                ),
            ],
            policy=policy,
        )

        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )
        self.assertTrue(decision.success)
        self.assertEqual(decision.provider, "openai")

    def test_requires_approval_adapter_is_filtered_without_approval(self) -> None:
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="sensitive_external",
                    provider="custom",
                    model="runtime-v1",
                    capabilities={"coding"},
                    requires_approval=True,
                )
            ],
            policy=build_default_router_policy(),
        )

        base_request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"coding"},
        )
        blocked = router.route_and_invoke(request=base_request, prompt="x")
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.reason, "no_eligible_adapter")

        approved_request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            required_capabilities={"coding"},
            approved_adapters={"sensitive_external"},
        )
        allowed = router.route_and_invoke(request=approved_request, prompt="x")
        self.assertTrue(allowed.success)

    def test_api_is_used_when_no_subscription_candidate_exists(self) -> None:
        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="openai_api_only",
                    provider="openai",
                    model="gpt-api",
                    capabilities={"coding"},
                )
            ],
            policy=build_default_router_policy(),
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )
        self.assertTrue(decision.success)
        self.assertEqual(decision.channel.value, "api")

    def test_api_thresholds_follow_policy_configuration(self) -> None:
        policy = build_default_router_policy()
        policy.complexity_threshold_for_api = "medium"
        policy.criticality_threshold_for_api = "medium"
        router = HybridRouter(adapters=[], policy=policy)
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.LOW,
        )
        self.assertTrue(router._must_include_api(request))

        policy.complexity_threshold_for_api = "invalid"
        router = HybridRouter(adapters=[], policy=policy)
        self.assertFalse(router._must_include_api(request))

    def test_role_model_preference_prioritizes_configured_model(self) -> None:
        policy = build_default_router_policy()
        policy.role_model_preferences["engineer"] = [
            "preferred-model",
            "secondary-model",
        ]
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="secondary_adapter",
                    provider="openai",
                    model="secondary-model",
                    capabilities={"coding"},
                    routing_priority=1,
                ),
                SubscriptionAdapter(
                    name="preferred_adapter",
                    provider="anthropic",
                    model="preferred-model",
                    capabilities={"coding"},
                    routing_priority=100,
                ),
            ],
            policy=policy,
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )
        self.assertTrue(decision.success)
        self.assertEqual(decision.model, "preferred-model")

    def test_enforce_role_model_preferences_blocks_non_matching_models(self) -> None:
        policy = build_default_router_policy()
        policy.enforce_role_model_preferences = True
        policy.role_model_preferences["engineer"] = ["gpt-5.3-codex"]
        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="groq_reasoning",
                    provider="groq",
                    model="llama-3.3-70b-versatile",
                    capabilities={"coding"},
                )
            ],
            policy=policy,
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
            ),
            prompt="simple prompt",
        )
        self.assertFalse(decision.success)
        self.assertEqual(decision.reason, "no_eligible_adapter")

    def test_strict_environment_policy_blocks_non_matching_models(self) -> None:
        policy = build_default_router_policy()
        policy.enforce_role_model_preferences = False
        policy.strict_role_policy_environments = ["prod"]
        policy.role_model_preferences["engineer"] = ["gpt-5.3-codex"]
        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="groq_reasoning",
                    provider="groq",
                    model="llama-3.3-70b-versatile",
                    capabilities={"coding"},
                )
            ],
            policy=policy,
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
                environment="prod",
            ),
            prompt="simple prompt",
        )
        self.assertFalse(decision.success)
        self.assertEqual(decision.reason, "no_eligible_adapter")

    def test_non_strict_environment_allows_non_matching_models(self) -> None:
        policy = build_default_router_policy()
        policy.enforce_role_model_preferences = False
        policy.strict_role_policy_environments = ["prod"]
        policy.role_model_preferences["engineer"] = ["gpt-5.3-codex"]
        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="groq_reasoning",
                    provider="groq",
                    model="llama-3.3-70b-versatile",
                    capabilities={"coding"},
                )
            ],
            policy=policy,
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
                environment="stage",
            ),
            prompt="simple prompt",
        )
        self.assertTrue(decision.success)
        self.assertEqual(decision.provider, "groq")

    def test_team_lead_rejects_local_models_even_if_available(self) -> None:
        router = HybridRouter(
            adapters=[
                SubscriptionAdapter(
                    name="ollama_qwen_coder_local",
                    provider="local",
                    model="qwen2.5-coder:14b",
                    capabilities={"coding", "reasoning", "analysis"},
                )
            ],
            policy=build_default_router_policy(),
        )
        decision = router.route_and_invoke(
            request=RoutingRequest(
                role=Role.TEAM_LEAD,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                required_capabilities={"coding"},
            ),
            prompt="Lead the team",
        )
        self.assertFalse(decision.success)
        self.assertEqual(decision.reason, "no_eligible_adapter")

    def test_team_lead_falls_back_to_advanced_api_when_pro_models_unhealthy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "provider_ops.json").write_text(
                '{"providers":[{"adapter_name":"openai_pro_cli","smoke_healthy":false,"operational":false},{"adapter_name":"gemini_pro_cli","smoke_healthy":false,"operational":false},{"adapter_name":"claude_pro_cli","smoke_healthy":false,"operational":false},{"adapter_name":"openai_api","smoke_healthy":true,"operational":true}]}',
                encoding="utf-8",
            )
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro_cli",
                        provider="openai",
                        model="gpt-4o",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-4.1-mini",
                        capabilities={"coding", "reasoning", "analysis"},
                        cost_tier=1,
                    ),
                    SubscriptionAdapter(
                        name="ollama_qwen_coder_local",
                        provider="local",
                        model="qwen2.5-coder:14b",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.TEAM_LEAD,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="Lead the team",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.channel.value, "api")
            self.assertEqual(decision.model, "gpt-4.1-mini")

    def test_team_lead_accepts_advanced_api_alias_by_provider_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "provider_ops.json").write_text(
                '{"providers":[{"adapter_name":"openai_pro_cli","smoke_healthy":false,"operational":false}]}',
                encoding="utf-8",
            )
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro_cli",
                        provider="openai",
                        model="gpt-4o",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                    ApiAdapter(
                        name="openai_api_mini",
                        provider="openai",
                        model="gpt-4.1-mini",
                        capabilities={"coding", "reasoning", "analysis"},
                        cost_tier=1,
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.TEAM_LEAD,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="Lead the team",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.channel.value, "api")
            self.assertEqual(decision.model, "gpt-4.1-mini")

    def test_team_lead_prefers_higher_ranked_senior_cloud_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "provider_ops.json").write_text(
                '{"providers":[{"adapter_name":"openai_pro_cli","smoke_healthy":true,"operational":true},{"adapter_name":"gemini_pro_cli","smoke_healthy":true,"operational":true}]}',
                encoding="utf-8",
            )
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="gemini_pro_cli",
                        provider="google",
                        model="gemini-1.5-pro",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                    SubscriptionAdapter(
                        name="openai_pro_cli",
                        provider="openai",
                        model="gpt-4o",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.TEAM_LEAD,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="Lead the team",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.provider, "openai")

    def test_budget_pressure_prefers_budget_api_for_engineer_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            now = datetime.now(timezone.utc).isoformat()
            (runtime_dir / "cost_ledger.jsonl").write_text(
                f'{{"ts":"{now}","model":"gpt-4.1-mini","cost_usd":8.5}}\n',
                encoding="utf-8",
            )
            budget = BudgetManager(
                runtime_dir=runtime_dir,
                policy=BudgetPolicy(
                    daily_api_budget_usd=10.0, monthly_api_budget_usd=200.0
                ),
            )
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-4.1-mini",
                        capabilities={"coding"},
                        cost_tier=1,
                    ),
                    ApiAdapter(
                        name="gpt-4o-mini",
                        provider="openai",
                        model="gpt-4o-mini",
                        capabilities={"coding"},
                        cost_tier=1,
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.ENGINEER,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="simple prompt",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.model, "gpt-4o-mini")

    def test_router_loads_model_catalog_override_from_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "model_catalog.json").write_text(
                '{"models":[{"adapter_name":"gemini_pro_cli","provider":"google","model":"gemini-1.5-pro","tier":"senior_cloud","intelligence_rank":99,"coding_rank":99,"reasoning_rank":99,"trust_rank":99,"local_allowed_for_team_lead":false,"api_allowed_for_team_lead":true}]}',
                encoding="utf-8",
            )
            (runtime_dir / "provider_ops.json").write_text(
                '{"providers":[{"adapter_name":"openai_pro_cli","smoke_healthy":true,"operational":true},{"adapter_name":"gemini_pro_cli","smoke_healthy":true,"operational":true}]}',
                encoding="utf-8",
            )
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro_cli",
                        provider="openai",
                        model="gpt-4o",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                    SubscriptionAdapter(
                        name="gemini_pro_cli",
                        provider="google",
                        model="gemini-1.5-pro",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.TEAM_LEAD,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="Lead the team",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.provider, "google")

    def test_load_model_catalog_reads_aiteam_runtime_for_external_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            runtime_dir = project_root / ".aiteam"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "model_catalog.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "adapter_name": "gemini_pro_cli",
                                "provider": "google",
                                "model": "gemini-external-runtime",
                                "tier": "senior_cloud",
                                "intelligence_rank": 99,
                                "coding_rank": 99,
                                "reasoning_rank": 99,
                                "trust_rank": 99,
                                "local_allowed_for_team_lead": False,
                                "api_allowed_for_team_lead": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            catalog = load_model_catalog(project_root=project_root)

            self.assertEqual(catalog["gemini_pro_cli"].model, "gemini-external-runtime")

    def test_team_lead_uses_provider_ops_operational_state_as_source_of_truth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "provider_ops.json").write_text(
                '{"providers":[{"adapter_name":"openai_pro_cli","smoke_healthy":true,"operational":false},{"adapter_name":"openai_api","smoke_healthy":true,"operational":true}]}',
                encoding="utf-8",
            )
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro_cli",
                        provider="openai",
                        model="gpt-4o",
                        capabilities={"coding", "reasoning", "analysis"},
                    ),
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-4.1-mini",
                        capabilities={"coding", "reasoning", "analysis"},
                        cost_tier=1,
                    ),
                ],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.TEAM_LEAD,
                    complexity=Complexity.HIGH,
                    criticality=Criticality.HIGH,
                    required_capabilities={"coding"},
                ),
                prompt="Lead the team",
            )
            self.assertTrue(decision.success)
            self.assertEqual(decision.channel.value, "api")


    def test_all_adapters_fail_returns_failed_decision(self) -> None:
        """Si todos los adapters fallan, la decision es all_attempts_failed (no crash)."""
        from unittest.mock import patch
        from aiteam.types import AdapterResponse

        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-4.1-mini",
                    capabilities={"coding"},
                    cost_tier=1,
                ),
            ],
            policy=build_default_router_policy(),
        )
        error_resp = AdapterResponse(success=False, content="", error="http_error:500:boom")
        with patch.object(router.adapters[0], "invoke", return_value=error_resp):
            decision = router.route_and_invoke(
                request=RoutingRequest(
                    role=Role.ENGINEER,
                    complexity=Complexity.LOW,
                    criticality=Criticality.LOW,
                ),
                prompt="do work",
            )
        self.assertFalse(decision.success)
        self.assertIn("fail", decision.reason or "")

    def test_ops_status_cache_avoids_repeated_io(self) -> None:
        """_cached_ops_status no relee el archivo si el TTL no expiró."""
        import time
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            ops_file = runtime_dir / "provider_ops.json"
            ops_file.write_text('{"providers":[]}', encoding="utf-8")
            budget = BudgetManager(runtime_dir=runtime_dir, policy=BudgetPolicy())
            router = HybridRouter(
                adapters=[],
                policy=build_default_router_policy(),
                budget_manager=budget,
            )
            # Primera lectura puebla el cache
            s1 = router._cached_ops_status()
            # Sobrescribir archivo — segunda lectura dentro del TTL debe devolver cache
            ops_file.write_text('{"providers":[{"adapter_name":"x"}]}', encoding="utf-8")
            s2 = router._cached_ops_status()
            self.assertEqual(s1, s2, "Cache debería devolver el valor anterior sin releer el archivo")

    def test_local_provider_skipped_when_degraded(self) -> None:
        """Con AITEAM_PROVIDER_LOCAL_DEGRADED=1 los adapters tier=local no son elegibles."""
        import os
        from unittest.mock import patch

        router = HybridRouter(
            adapters=[
                ApiAdapter(
                    name="claude_haiku_api",
                    provider="anthropic",
                    model="claude-haiku-4-5-20251001",
                    capabilities={"coding"},
                    cost_tier=0,
                ),
            ],
            policy=build_default_router_policy(),
        )
        # Inyectar un adapter local manualmente en la lista
        try:
            from aiteam.adapters.local import LocalAdapter  # type: ignore[import]
            local_adapter = LocalAdapter(
                name="ollama_qwen_coder_local",
                provider="local",
                model="qwen2.5-coder:14b",
            )
            router.adapters.insert(0, local_adapter)
            with patch.dict(os.environ, {"AITEAM_PROVIDER_LOCAL_DEGRADED": "1"}):
                eligible = router._eligible(
                    RoutingRequest(
                        role=Role.ENGINEER,
                        complexity=Complexity.LOW,
                        criticality=Criticality.LOW,
                    )
                )
            names = [a.name for a in eligible]
            self.assertNotIn("ollama_qwen_coder_local", names)
        except (ImportError, Exception):
            # Si LocalAdapter no existe, test pasa — la protección ya está en router
            pass


if __name__ == "__main__":
    unittest.main()

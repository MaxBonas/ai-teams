from __future__ import annotations

from dataclasses import dataclass

from aiteam.adapters.base import ModelAdapter
from aiteam.config import RouterPolicy
from aiteam.finops import BudgetManager
from aiteam.observability import EventLogger
from aiteam.types import ChannelType, Complexity, Criticality, RoutingDecision, RoutingRequest


@dataclass
class AdapterAttempt:
    adapter_name: str
    channel: ChannelType
    success: bool
    error: str | None


class HybridRouter:
    """Router Pro-first con fallback API."""

    def __init__(
        self,
        adapters: list[ModelAdapter],
        policy: RouterPolicy,
        budget_manager: BudgetManager | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self.adapters = adapters
        self.policy = policy
        self.budget_manager = budget_manager
        self.event_logger = event_logger

    def _eligible(self, request: RoutingRequest) -> list[ModelAdapter]:
        eligible = []
        role_key = request.role.value
        request_env = str(request.environment).strip().lower()
        strict_envs = {
            item.strip().lower()
            for item in self.policy.strict_role_policy_environments
            if str(item).strip()
        }
        enforce_role_policy = self.policy.enforce_role_model_preferences or request_env in strict_envs
        role_model_preferences = {
            item.strip().lower()
            for item in self.policy.role_model_preferences.get(role_key, [])
            if str(item).strip()
        }
        role_provider_preferences = {
            item.strip().lower()
            for item in self.policy.role_provider_preferences.get(role_key, [])
            if str(item).strip()
        }
        for adapter in self.adapters:
            if adapter.role_targets and request.role.value not in adapter.role_targets:
                continue
            if enforce_role_policy:
                if role_model_preferences and adapter.model.strip().lower() not in role_model_preferences:
                    continue
                if role_provider_preferences and adapter.provider.strip().lower() not in role_provider_preferences:
                    continue
            if adapter.requires_approval and not (
                request.sensitive_approval or adapter.name in request.approved_adapters
            ):
                continue
            if request.required_capabilities and not request.required_capabilities.issubset(
                adapter.capabilities
            ):
                continue
            if not adapter.available():
                continue
            eligible.append(adapter)
        return eligible

    def _sort_adapters(self, adapters: list[ModelAdapter], request: RoutingRequest) -> list[ModelAdapter]:
        provider_priority_sub = {
            provider: i
            for i, provider in enumerate(self.policy.preferred_subscription_providers)
        }
        provider_priority_api = {
            provider: i for i, provider in enumerate(self.policy.preferred_api_providers)
        }
        role_key = request.role.value
        role_model_priority = {
            str(model).strip().lower(): idx
            for idx, model in enumerate(self.policy.role_model_preferences.get(role_key, []))
            if str(model).strip()
        }
        role_provider_priority = {
            str(provider).strip().lower(): idx
            for idx, provider in enumerate(self.policy.role_provider_preferences.get(role_key, []))
            if str(provider).strip()
        }

        def key(adapter: ModelAdapter):
            if adapter.channel == ChannelType.SUBSCRIPTION:
                provider_rank = provider_priority_sub.get(adapter.provider, 99)
                channel_rank = 0 if self.policy.pro_first else 1
            else:
                provider_rank = provider_priority_api.get(adapter.provider, 99)
                channel_rank = 1 if self.policy.pro_first else 0
            role_model_rank = role_model_priority.get(adapter.model.strip().lower(), 999)
            role_provider_rank = role_provider_priority.get(adapter.provider.strip().lower(), 999)
            return (
                channel_rank,
                role_model_rank,
                role_provider_rank,
                adapter.routing_priority,
                provider_rank,
                adapter.cost_tier,
                adapter.name,
            )

        return sorted(adapters, key=key)

    def _must_include_api(self, request: RoutingRequest) -> bool:
        if not self.policy.pro_first:
            return True
        return self._meets_complexity_threshold(request.complexity) or self._meets_criticality_threshold(
            request.criticality
        )

    def _meets_complexity_threshold(self, complexity: Complexity) -> bool:
        threshold = self._complexity_from_policy(self.policy.complexity_threshold_for_api)
        return self._complexity_rank(complexity) >= self._complexity_rank(threshold)

    def _meets_criticality_threshold(self, criticality: Criticality) -> bool:
        threshold = self._criticality_from_policy(self.policy.criticality_threshold_for_api)
        return self._criticality_rank(criticality) >= self._criticality_rank(threshold)

    @staticmethod
    def _complexity_from_policy(value: str) -> Complexity:
        normalized = str(value).strip().lower()
        try:
            return Complexity(normalized)
        except ValueError:
            return Complexity.HIGH

    @staticmethod
    def _criticality_from_policy(value: str) -> Criticality:
        normalized = str(value).strip().lower()
        try:
            return Criticality(normalized)
        except ValueError:
            return Criticality.HIGH

    @staticmethod
    def _complexity_rank(value: Complexity) -> int:
        ranking = {
            Complexity.LOW: 1,
            Complexity.MEDIUM: 2,
            Complexity.HIGH: 3,
        }
        return ranking.get(value, 3)

    @staticmethod
    def _criticality_rank(value: Criticality) -> int:
        ranking = {
            Criticality.LOW: 1,
            Criticality.MEDIUM: 2,
            Criticality.HIGH: 3,
        }
        return ranking.get(value, 3)

    def _get_model_daily_spend(self) -> dict[str, float]:
        """Get daily spend totals by model from ledger."""
        if not self.budget_manager:
            return {}
        return self.budget_manager.daily_spend_by_model()

    def route_and_invoke(
        self,
        request: RoutingRequest,
        prompt: str,
        task_id: str = "",
    ) -> RoutingDecision:
        attempts: list[str] = []
        attempted_channels: dict[ChannelType, int] = {
            ChannelType.SUBSCRIPTION: 0,
            ChannelType.API: 0,
        }

        eligible = self._sort_adapters(self._eligible(request), request)
        if not eligible:
            decision = RoutingDecision(
                success=False,
                provider="none",
                model="none",
                channel=ChannelType.API,
                reason="no_eligible_adapter",
                response=self._failed_response("No adapters available"),
                attempts=attempts,
            )
            self._record_decision(decision, task_id=task_id)
            return decision

        must_include_api = self._must_include_api(request)
        has_subscription_candidate = any(
            adapter.channel == ChannelType.SUBSCRIPTION for adapter in eligible
        )
        api_attempt_limit = self.policy.max_api_attempts
        max_api_cost_tier = 999
        if self.budget_manager is not None:
            signal = self.budget_manager.api_signal()
            api_attempt_limit = min(api_attempt_limit, signal.suggested_max_api_attempts)
            max_api_cost_tier = signal.max_api_cost_tier

        # Get per-model daily spend limits
        model_daily_spend = self._get_model_daily_spend()
        model_daily_caps = (
            self.budget_manager.policy.per_model_daily_cap_usd or {}
            if self.budget_manager
            else {}
        )

        for adapter in eligible:
            if adapter.channel == ChannelType.SUBSCRIPTION:
                if attempted_channels[ChannelType.SUBSCRIPTION] >= self.policy.max_subscription_attempts:
                    continue
            else:
                if not self.policy.api_fallback_enabled:
                    continue
                if self.budget_manager is not None:
                    signal = self.budget_manager.api_signal()
                    if not signal.can_use_api:
                        attempts.append(f"api_budget_block:{signal.reason}")
                        continue
                    if adapter.cost_tier > signal.max_api_cost_tier:
                        attempts.append(
                            f"api_tier_block:{adapter.name}:tier{adapter.cost_tier}>max{signal.max_api_cost_tier}"
                        )
                        continue
                    # Check per-model daily cap
                    if adapter.model in model_daily_caps:
                        cap = model_daily_caps[adapter.model]
                        spend = model_daily_spend.get(adapter.model, 0.0)
                        if spend >= cap:
                            attempts.append(
                                f"model_cap_block:{adapter.model}:spend${spend:.2f}>=cap${cap:.2f}"
                            )
                            continue
                if (
                    not must_include_api
                    and attempted_channels[ChannelType.SUBSCRIPTION] == 0
                    and self.policy.pro_first
                    and has_subscription_candidate
                ):
                    continue
                if attempted_channels[ChannelType.API] >= api_attempt_limit:
                    continue

            attempted_channels[adapter.channel] += 1
            response = adapter.invoke(prompt)
            attempts.append(
                f"{adapter.name}:{adapter.channel.value}:{'ok' if response.success else 'fail'}"
            )
            if response.success:
                decision = RoutingDecision(
                    success=True,
                    provider=adapter.provider,
                    model=adapter.model,
                    channel=adapter.channel,
                    reason="selected_by_policy",
                    response=response,
                    attempts=attempts,
                )
                self._record_decision(decision, task_id=task_id)
                return decision

        decision = RoutingDecision(
            success=False,
            provider="none",
            model="none",
            channel=ChannelType.API,
            reason="all_attempts_failed",
            response=self._failed_response("All adapter attempts failed"),
            attempts=attempts,
        )
        self._record_decision(decision, task_id=task_id)
        return decision

    @staticmethod
    def _failed_response(message: str):
        from aiteam.types import AdapterResponse

        return AdapterResponse(success=False, content="", error=message, latency_ms=0)

    def _record_decision(self, decision: RoutingDecision, *, task_id: str = "") -> None:
        if self.budget_manager is not None:
            self.budget_manager.record_routing_decision(decision)
        if self.event_logger is not None:
            self.event_logger.emit(
                "routing_decision",
                {
                    "task_id": str(task_id or ""),
                    "success": decision.success,
                    "provider": decision.provider,
                    "model": decision.model,
                    "channel": decision.channel.value,
                    "reason": decision.reason,
                    "attempts": decision.attempts,
                },
            )

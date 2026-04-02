from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
from pathlib import Path
import threading
import time

from aiteam.adapters.base import ModelAdapter
from aiteam.config import RouterPolicy
from aiteam.finops import BudgetManager
from aiteam.model_catalog import load_model_catalog
from aiteam.observability import EventLogger
from aiteam.persistence import AtomicFileWriter
from aiteam.provider_ops import provider_ops_status
from aiteam.types import (
    ChannelType,
    Complexity,
    Criticality,
    Role,
    RoutingDecision,
    RoutingRequest,
    StreamChunk,
)


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
        self.runtime_dir = None
        # Cache TTL para provider_ops_status — evita I/O en cada routing
        self._ops_cache: dict = {}
        self._ops_cache_populated: bool = False
        self._ops_cache_ts: float = 0.0
        self._ops_cache_ttl: float = 30.0
        self._ops_cache_lock = threading.Lock()
        self._tool_economics_cache: dict[str, dict[str, float]] = {}
        self._tool_economics_cache_populated: bool = False
        self._tool_economics_cache_ts: float = 0.0
        self._tool_economics_cache_ttl: float = 30.0
        self._tool_economics_cache_lock = threading.Lock()
        self._routing_failures: list[dict[str, str]] = []
        self._routing_failures_lock = threading.Lock()
        if self.budget_manager is not None:
            self.runtime_dir = self.budget_manager.runtime_dir
        project_root = None
        if self.runtime_dir is not None:
            project_root = Path(self.runtime_dir).parent
        self.model_catalog = load_model_catalog(
            project_root,
            Path(self.runtime_dir) if self.runtime_dir is not None else None,
        )

    def _profile_for(self, adapter: ModelAdapter):
        profile = self.model_catalog.get(adapter.name)
        if profile is not None:
            return profile
        provider = adapter.provider.strip().lower()
        model = adapter.model.strip().lower()
        matches = [
            item
            for item in self.model_catalog.values()
            if item.provider == provider and item.model.strip().lower() == model
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _cached_ops_status(self) -> dict:
        """Devuelve provider_ops_status con TTL cache de 30s para evitar I/O por routing."""
        if self.runtime_dir is None:
            return {}
        now = time.monotonic()
        with self._ops_cache_lock:
            if now - self._ops_cache_ts < self._ops_cache_ttl and self._ops_cache_populated:
                return self._ops_cache
            status = provider_ops_status(Path(self.runtime_dir)) or {}
            self._ops_cache = status
            self._ops_cache_populated = True
            self._ops_cache_ts = now
            return status

    def _smoke_ok(self, adapter: ModelAdapter) -> bool:
        status = self._cached_ops_status()
        if not status:
            return True
        if adapter.name in status:
            return bool(status[adapter.name].get("smoke_healthy", False))
        return True

    def _operational_ok(self, adapter: ModelAdapter) -> bool:
        status = self._cached_ops_status()
        if not status:
            return True
        if adapter.name in status:
            return bool(status[adapter.name].get("operational", False))
        return True

    def _team_lead_allowed(self, adapter: ModelAdapter) -> bool:
        profile = self._profile_for(adapter)
        if profile is None:
            return False
        if profile.tier == "senior_cloud":
            return self._smoke_ok(adapter)
        if profile.tier == "advanced_api" and profile.api_allowed_for_team_lead:
            return self._smoke_ok(adapter)
        return False

    def _role_rank(self, adapter: ModelAdapter, role_key: str) -> int:
        profile = self._profile_for(adapter)
        if profile is None:
            return 999
        if role_key == "team_lead":
            return -(profile.reasoning_rank + profile.coding_rank + profile.trust_rank)
        if role_key == "engineer":
            return -(profile.coding_rank + profile.reasoning_rank)
        if role_key == "reviewer":
            return -(profile.reasoning_rank + profile.trust_rank)
        if role_key == "researcher":
            return -(profile.reasoning_rank + profile.intelligence_rank)
        if role_key == "scout":
            # Scout prioriza velocidad y bajo coste sobre calidad
            return -profile.intelligence_rank
        return -(profile.trust_rank + profile.reasoning_rank)

    def _api_pressure(self) -> float:
        if self.budget_manager is None:
            return 0.0
        signal = self.budget_manager.api_signal()
        return max(signal.daily_utilization_ratio, signal.monthly_utilization_ratio)

    def _tier_rank(self, adapter: ModelAdapter, request: RoutingRequest) -> int:
        if self._prefer_tool_economy(request):
            return self._tool_economy_tier_rank(adapter, request)
        profile = self._profile_for(adapter)
        if profile is None:
            return 50
        if request.role == Role.TEAM_LEAD:
            order = {
                "senior_cloud": 0,
                "advanced_api": 1,
                "budget_api": 9,
                "local": 99,
            }
            return order.get(profile.tier, 50)
        if request.role.value == "scout":
            # Scout SIEMPRE usa el modelo mas barato disponible
            order = {
                "budget_api": 0,
                "local": 1,
                "advanced_api": 8,
                "senior_cloud": 99,
            }
            return order.get(profile.tier, 50)
        pressure = self._api_pressure()
        if pressure >= 0.75:
            order = {
                "budget_api": 0,
                "advanced_api": 1,
                "senior_cloud": 2,
                "local": 3,
            }
            return order.get(profile.tier, 50)
        if pressure >= 0.5:
            order = {
                "senior_cloud": 0,
                "budget_api": 1,
                "advanced_api": 2,
                "local": 3,
            }
            return order.get(profile.tier, 50)
        order = {
            "senior_cloud": 0,
            "advanced_api": 1,
            "budget_api": 2,
            "local": 3,
        }
        return order.get(profile.tier, 50)

    def _adapter_tier_label(self, adapter: ModelAdapter) -> str:
        profile = self._profile_for(adapter)
        if profile is not None:
            return profile.tier
        if adapter.channel == ChannelType.SUBSCRIPTION:
            return "senior_cloud"
        if adapter.cost_tier <= 1:
            return "budget_api"
        if adapter.cost_tier <= 2:
            return "advanced_api"
        return "budget_api"

    @staticmethod
    def _preferred_tool_tier(request: RoutingRequest) -> str:
        return str(getattr(request, "preferred_tool_tier", "") or "").strip().lower()

    def _cached_tool_economics(self) -> dict[str, dict[str, float]]:
        if self.runtime_dir is None:
            return {}
        now = time.monotonic()
        with self._tool_economics_cache_lock:
            if (
                now - self._tool_economics_cache_ts < self._tool_economics_cache_ttl
                and self._tool_economics_cache_populated
            ):
                return self._tool_economics_cache
            events_path = Path(self.runtime_dir) / "events.jsonl"
            if not events_path.exists():
                self._tool_economics_cache = {}
                self._tool_economics_cache_populated = True
                self._tool_economics_cache_ts = now
                return {}
            records = AtomicFileWriter.read_jsonl_with_dedup(events_path)
            specialist_rows: dict[str, dict[str, float]] = {}
            for record in records:
                if str(record.get("event_type", "")) != "delegate_economics_estimated":
                    continue
                payload = record.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                quorum_met = bool(payload.get("quorum_met", False))
                specialist_breakdown = payload.get("specialist_breakdown", {})
                if not isinstance(specialist_breakdown, dict):
                    continue
                for raw_name, raw_values in specialist_breakdown.items():
                    if not isinstance(raw_values, dict):
                        continue
                    specialist_name = str(raw_name or "").strip().lower()
                    if not specialist_name:
                        continue
                    row = specialist_rows.setdefault(
                        specialist_name,
                        {
                            "batches": 0.0,
                            "count": 0.0,
                            "completed": 0.0,
                            "failed": 0.0,
                            "quorum_met_count": 0.0,
                            "estimated_net_tokens_saved": 0.0,
                            "estimated_cost_units_saved": 0.0,
                        },
                    )
                    row["batches"] += 1.0
                    row["count"] += float(raw_values.get("count", 0) or 0)
                    row["completed"] += float(raw_values.get("completed", 0) or 0)
                    row["failed"] += float(raw_values.get("failed", 0) or 0)
                    row["estimated_net_tokens_saved"] += float(
                        raw_values.get("estimated_net_tokens_saved", 0) or 0
                    )
                    row["estimated_cost_units_saved"] += float(
                        raw_values.get("estimated_cost_units_saved", 0) or 0
                    )
                    if quorum_met:
                        row["quorum_met_count"] += 1.0
            for row in specialist_rows.values():
                batches = max(1.0, row["batches"])
                count = max(1.0, row["count"])
                row["quorum_met_ratio"] = round(row["quorum_met_count"] / batches, 4)
                row["avg_net_tokens_saved"] = round(
                    row["estimated_net_tokens_saved"] / count,
                    2,
                )
                row["avg_cost_units_saved"] = round(
                    row["estimated_cost_units_saved"] / count,
                    2,
                )
            self._tool_economics_cache = specialist_rows
            self._tool_economics_cache_populated = True
            self._tool_economics_cache_ts = now
            return self._tool_economics_cache

    def _effective_tool_tier(self, request: RoutingRequest) -> str:
        preferred_tier = self._preferred_tool_tier(request)
        specialist_name = str(
            getattr(request, "tool_rewiring_preferred_specialist", "") or ""
        ).strip().lower() or str(getattr(request, "tool_specialist", "") or "").strip().lower()
        if not specialist_name:
            return preferred_tier
        specialist_metrics = self._cached_tool_economics().get(specialist_name, {})
        batches = int(specialist_metrics.get("batches", 0.0))
        quorum_ratio = float(specialist_metrics.get("quorum_met_ratio", 0.0) or 0.0)
        avg_net_saved = float(
            specialist_metrics.get("avg_net_tokens_saved", 0.0) or 0.0
        )
        if batches >= 2 and (quorum_ratio < 0.5 or avg_net_saved < 250.0):
            return "advanced_api"
        return preferred_tier

    def _prefer_tool_economy(self, request: RoutingRequest) -> bool:
        if request.role == Role.TEAM_LEAD:
            return False
        if bool(getattr(request, "prefer_economic_routing", False)):
            return True
        return bool(
            str(getattr(request, "tool_specialist", "") or "").strip()
            or str(getattr(request, "tool_rewiring_preferred_specialist", "") or "").strip()
        )

    def _tool_economy_tier_rank(self, adapter: ModelAdapter, request: RoutingRequest) -> int:
        tier = self._adapter_tier_label(adapter)
        preferred_tier = self._effective_tool_tier(request)
        if preferred_tier == "local":
            order = {
                "local": 0,
                "budget_api": 1,
                "advanced_api": 2,
                "senior_cloud": 9,
            }
        elif preferred_tier == "advanced_api":
            order = {
                "advanced_api": 0,
                "budget_api": 1,
                "local": 2,
                "senior_cloud": 9,
            }
        else:
            order = {
                "budget_api": 0,
                "local": 1,
                "advanced_api": 2,
                "senior_cloud": 9,
            }
        return order.get(tier, 50)

    def _eligible(self, request: RoutingRequest) -> list[ModelAdapter]:
        eligible = []
        role_key = request.role.value
        request_env = str(request.environment).strip().lower()
        strict_envs = {
            item.strip().lower()
            for item in self.policy.strict_role_policy_environments
            if str(item).strip()
        }
        enforce_role_policy = (
            self.policy.enforce_role_model_preferences or request_env in strict_envs
        )
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
        role_provider_exclusions = {
            item.strip().lower()
            for item in self.policy.role_provider_exclusions.get(role_key, [])
            if str(item).strip()
        }
        for adapter in self.adapters:
            if adapter.name in request.excluded_adapters:
                continue
            if adapter.provider.strip().lower() in request.excluded_providers:
                continue
            if adapter.provider.strip().lower() in role_provider_exclusions:
                continue
            if adapter.role_targets and request.role.value not in adapter.role_targets:
                continue
            if request.role == Role.TEAM_LEAD and not self._team_lead_allowed(adapter):
                continue
            if not self._operational_ok(adapter) and request.role == Role.TEAM_LEAD:
                continue
            if enforce_role_policy:
                if (
                    role_model_preferences
                    and adapter.model.strip().lower() not in role_model_preferences
                ):
                    continue
                if (
                    role_provider_preferences
                    and adapter.provider.strip().lower()
                    not in role_provider_preferences
                ):
                    continue
            if adapter.requires_approval and not (
                request.sensitive_approval or adapter.name in request.approved_adapters
            ):
                continue
            if (
                request.required_capabilities
                and not request.required_capabilities.issubset(adapter.capabilities)
            ):
                continue
            if not adapter.available():
                continue
            # Skip local-tier adapters si la maquina no tiene inferencia local disponible
            # (AITEAM_PROVIDER_LOCAL_DEGRADED=1 en ORCH-01; 0 en max-gamingpc con Ollama)
            profile = self._profile_for(adapter)
            if profile and profile.tier == "local":
                if os.getenv("AITEAM_PROVIDER_LOCAL_DEGRADED", "0") == "1":
                    continue
            eligible.append(adapter)
        return eligible

    def eligible_adapters(self, request: RoutingRequest) -> list[ModelAdapter]:
        """Expone el pool elegible ya ordenado para capas de orquestación."""
        return self._sort_adapters(self._eligible(request), request)

    def _sort_adapters(
        self, adapters: list[ModelAdapter], request: RoutingRequest
    ) -> list[ModelAdapter]:
        provider_priority_sub = {
            provider: i
            for i, provider in enumerate(self.policy.preferred_subscription_providers)
        }
        provider_priority_api = {
            provider: i
            for i, provider in enumerate(self.policy.preferred_api_providers)
        }
        role_key = request.role.value
        role_model_priority = {
            str(model).strip().lower(): idx
            for idx, model in enumerate(
                self.policy.role_model_preferences.get(role_key, [])
            )
            if str(model).strip()
        }
        role_provider_priority = {
            str(provider).strip().lower(): idx
            for idx, provider in enumerate(
                self.policy.role_provider_preferences.get(role_key, [])
            )
            if str(provider).strip()
        }
        primary_provider = str(
            self.policy.role_primary_provider.get(role_key, "") or ""
        ).strip().lower()

        def key(adapter: ModelAdapter):
            provider_key = adapter.provider.strip().lower()
            if adapter.channel == ChannelType.SUBSCRIPTION:
                provider_rank = provider_priority_sub.get(adapter.provider, 99)
                channel_rank = 0 if self.policy.pro_first else 1
            else:
                provider_rank = provider_priority_api.get(adapter.provider, 99)
                channel_rank = 1 if self.policy.pro_first else 0
            primary_provider_rank = (
                0 if primary_provider and provider_key == primary_provider else 1
            )
            role_model_rank = role_model_priority.get(
                adapter.model.strip().lower(), 999
            )
            role_provider_rank = role_provider_priority.get(provider_key, 999)
            if self._prefer_tool_economy(request):
                return (
                    primary_provider_rank,
                    self._tier_rank(adapter, request),
                    channel_rank,
                    self._role_rank(adapter, role_key),
                    role_model_rank,
                    role_provider_rank,
                    adapter.routing_priority,
                    provider_rank,
                    adapter.cost_tier,
                    adapter.name,
                )
            return (
                primary_provider_rank,
                channel_rank,
                self._tier_rank(adapter, request),
                self._role_rank(adapter, role_key),
                role_model_rank,
                role_provider_rank,
                adapter.routing_priority,
                provider_rank,
                adapter.cost_tier,
                adapter.name,
            )

        return sorted(adapters, key=key)

    @staticmethod
    def _attempt_error_hint(error: str | None) -> str:
        text = str(error or "").strip()
        if not text:
            return ""
        compact = text.splitlines()[0].strip()
        compact = compact.replace(" ", "_")
        return compact[:96]

    def _record_routing_failure(
        self,
        *,
        task_id: str,
        request: RoutingRequest,
        adapter: ModelAdapter | None,
        error: str,
        reason: str,
    ) -> None:
        phase = task_id.split("::")[-1] if "::" in task_id else task_id
        entry = {
            "task_id": str(task_id or "").strip(),
            "phase": str(phase or "").strip(),
            "role": request.role.value,
            "provider": str(adapter.provider if adapter is not None else "").strip(),
            "model": str(adapter.model if adapter is not None else "").strip(),
            "adapter_name": str(adapter.name if adapter is not None else "").strip(),
            "error": str(error or "").strip(),
            "reason": str(reason or "").strip(),
        }
        with self._routing_failures_lock:
            self._routing_failures.append(entry)
            if len(self._routing_failures) > 200:
                self._routing_failures = self._routing_failures[-200:]

    def get_recent_routing_failures(self, task_root: str = "") -> list[dict[str, str]]:
        prefix = str(task_root or "").strip()
        with self._routing_failures_lock:
            items = list(self._routing_failures)
        if not prefix:
            return items
        return [
            item
            for item in items
            if str(item.get("task_id", "") or "").startswith(prefix)
        ]

    def get_missing_api_keys(self, task_root: str = "") -> list[str]:
        missing: set[str] = set()
        for item in self.get_recent_routing_failures(task_root):
            error = str(item.get("error", "") or "").strip()
            if not error.startswith("missing_api_key:"):
                continue
            _, _, env_name = error.partition(":")
            if env_name.strip():
                missing.add(env_name.strip())
        return sorted(missing)

    def get_unavailable_models(self, task_root: str = "") -> list[str]:
        unavailable: set[str] = set()
        for item in self.get_recent_routing_failures(task_root):
            error = str(item.get("error", "") or "").strip().lower()
            model = str(item.get("model", "") or "").strip()
            if not model:
                continue
            if error.startswith("missing_api_key:"):
                continue
            unavailable.add(model)
        return sorted(unavailable)

    def _must_include_api(self, request: RoutingRequest) -> bool:
        if self._prefer_tool_economy(request):
            return True
        if not self.policy.pro_first:
            return True
        return self._meets_complexity_threshold(
            request.complexity
        ) or self._meets_criticality_threshold(request.criticality)

    def _meets_complexity_threshold(self, complexity: Complexity) -> bool:
        threshold = self._complexity_from_policy(
            self.policy.complexity_threshold_for_api
        )
        return self._complexity_rank(complexity) >= self._complexity_rank(threshold)

    def _meets_criticality_threshold(self, criticality: Criticality) -> bool:
        threshold = self._criticality_from_policy(
            self.policy.criticality_threshold_for_api
        )
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
        messages: list[dict[str, str]] | None = None,
        tools=None,
        on_chunk: "Callable[[str | StreamChunk], None] | None" = None,
    ) -> RoutingDecision:
        attempts: list[str] = []
        attempted_channels: dict[ChannelType, int] = {
            ChannelType.SUBSCRIPTION: 0,
            ChannelType.API: 0,
        }

        eligible = self.eligible_adapters(request)
        if not eligible:
            self._record_routing_failure(
                task_id=task_id,
                request=request,
                adapter=None,
                error="no_eligible_adapter",
                reason="no_eligible_adapter",
            )
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
            api_attempt_limit = min(
                api_attempt_limit, signal.suggested_max_api_attempts
            )
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
                # Sin límite artificial de intentos para subscription: se prueba cada
                # adapter disponible hasta encontrar uno que funcione.
                # max_subscription_attempts se ignora — el fallback es el punto.
                pass
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
                # api_attempt_limit solo aplica si el budget manager sugiere reducir
                # llamadas por presión de coste. Sin presión, intentar todos.
                if self.budget_manager is not None and attempted_channels[ChannelType.API] >= api_attempt_limit:
                    continue

            attempted_channels[adapter.channel] += 1
            if on_chunk is not None:
                import time as _time
                started = _time.time()
                output_chunks: list[str] = []
                received_chunk = False
                try:
                    for chunk in adapter.invoke_stream(prompt, messages=messages):
                        received_chunk = True
                        on_chunk(chunk)
                        if isinstance(chunk, StreamChunk):
                            if chunk.chunk_type == "output" and chunk.text:
                                output_chunks.append(chunk.text)
                        elif chunk:
                            output_chunks.append(chunk)
                except Exception:
                    output_chunks = []
                    received_chunk = False
                if output_chunks:
                    content = "".join(output_chunks)
                    from aiteam.types import AdapterResponse
                    response = AdapterResponse(
                        success=True,
                        content=content,
                        latency_ms=int((_time.time() - started) * 1000),
                        input_tokens=max(1, len(prompt) // 4),
                        output_tokens=max(1, len(content) // 4),
                    )
                elif not received_chunk:
                    # Fallback a invoke normal si streaming no produjo chunks
                    invoke_params = inspect.signature(adapter.invoke).parameters
                    kwargs = {}
                    if "messages" in invoke_params:
                        kwargs["messages"] = messages
                    if "tools" in invoke_params and tools is not None:
                        kwargs["tools"] = tools
                    response = adapter.invoke(prompt, **kwargs)
                else:
                    invoke_params = inspect.signature(adapter.invoke).parameters
                    kwargs = {}
                    if "messages" in invoke_params:
                        kwargs["messages"] = messages
                    if "tools" in invoke_params and tools is not None:
                        kwargs["tools"] = tools
                    response = adapter.invoke(prompt, **kwargs)
            else:
                invoke_params = inspect.signature(adapter.invoke).parameters
                kwargs = {}
                if "messages" in invoke_params:
                    kwargs["messages"] = messages
                if "tools" in invoke_params and tools is not None:
                    kwargs["tools"] = tools
                response = adapter.invoke(prompt, **kwargs)
            attempt = (
                f"{adapter.name}:{adapter.channel.value}:"
                f"{'ok' if response.success else 'fail'}"
            )
            if not response.success:
                self._record_routing_failure(
                    task_id=task_id,
                    request=request,
                    adapter=adapter,
                    error=str(response.error or ""),
                    reason="adapter_invoke_failed",
                )
                error_hint = self._attempt_error_hint(response.error)
                if error_hint:
                    attempt = f"{attempt}:{error_hint}"
            attempts.append(attempt)
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

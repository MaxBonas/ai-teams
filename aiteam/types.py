from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChannelType(str, Enum):
    SUBSCRIPTION = "subscription"
    API = "api"


class TaskState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class Role(str, Enum):
    TEAM_LEAD = "team_lead"
    RESEARCHER = "researcher"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"
    QA = "qa"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Criticality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class WorkTask:
    task_id: str
    title: str
    description: str
    role: Role
    complexity: Complexity = Complexity.MEDIUM
    criticality: Criticality = Criticality.MEDIUM
    dependencies: list[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    assignee: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingRequest:
    role: Role
    complexity: Complexity
    criticality: Criticality
    required_capabilities: set[str] = field(default_factory=set)
    approved_adapters: set[str] = field(default_factory=set)
    sensitive_approval: bool = False
    environment: str = "dev"


@dataclass
class AdapterResponse:
    success: bool
    content: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class RoutingDecision:
    success: bool
    provider: str
    model: str
    channel: ChannelType
    reason: str
    response: AdapterResponse
    attempts: list[str] = field(default_factory=list)

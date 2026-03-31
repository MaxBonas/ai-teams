from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiteam.types import Complexity, Criticality, Role
from aiteam.workflow_planner import PhaseSpec


@dataclass(frozen=True)
class ChatRunState:
    """Estado canonico minimo de una corrida de chat."""

    chat_root: str
    lead_task_id: str
    preferred_role: Role
    chat_mode: str
    complexity: Complexity
    criticality: Criticality
    round_budget: int
    phases: list[PhaseSpec]
    phase_evidence_plan: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el estado para persistencia/reanudacion."""

        return {
            "chat_root": self.chat_root,
            "lead_task_id": self.lead_task_id,
            "preferred_role": self.preferred_role.value,
            "chat_mode": self.chat_mode,
            "complexity": self.complexity.value,
            "criticality": self.criticality.value,
            "round_budget": self.round_budget,
            "phases": [
                {
                    "phase_id": spec.phase_id,
                    "role": spec.role,
                    "objective": spec.objective,
                    "depends_on": list(spec.depends_on),
                }
                for spec in self.phases
            ],
            "phase_evidence_plan": self.phase_evidence_plan,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatRunState":
        """Reconstruye el estado desde un snapshot persistido."""

        phases = [
            PhaseSpec(
                phase_id=str(item.get("phase_id", "")).strip(),
                role=str(item.get("role", "")).strip(),
                objective=str(item.get("objective", "")).strip(),
                depends_on=[str(dep) for dep in list(item.get("depends_on") or [])],
            )
            for item in list(payload.get("phases") or [])
            if isinstance(item, dict)
        ]
        return cls(
            chat_root=str(payload.get("chat_root", "")).strip(),
            lead_task_id=str(payload.get("lead_task_id", "")).strip(),
            preferred_role=_coerce_role(payload.get("preferred_role", Role.TEAM_LEAD.value)),
            chat_mode=str(payload.get("chat_mode", "sprint5")).strip() or "sprint5",
            complexity=Complexity(str(payload.get("complexity", Complexity.MEDIUM.value))),
            criticality=Criticality(
                str(payload.get("criticality", Criticality.MEDIUM.value))
            ),
            round_budget=max(1, int(payload.get("round_budget", 1))),
            phases=phases,
            phase_evidence_plan={
                str(key): dict(value)
                for key, value in dict(payload.get("phase_evidence_plan") or {}).items()
                if str(key).strip() and isinstance(value, dict)
            },
        )

    @property
    def phase_task_ids(self) -> dict[str, str]:
        mapping: dict[str, str] = {"lead_intake": self.lead_task_id}
        for spec in self.phases:
            mapping[spec.phase_id] = f"{self.chat_root}::{spec.phase_id}"
        mapping["lead_close"] = f"{self.chat_root}::lead_close"
        return mapping

    @property
    def workflow_phase_keys(self) -> list[str]:
        return ["lead_intake"] + [spec.phase_id for spec in self.phases] + [
            "lead_close"
        ]

    @property
    def delegated_task_ids(self) -> list[str]:
        mapping = self.phase_task_ids
        return [mapping[spec.phase_id] for spec in self.phases]

    @property
    def lead_close_task_id(self) -> str:
        return self.phase_task_ids["lead_close"]

    def dependency_ids_for(self, spec: PhaseSpec) -> list[str]:
        mapping = self.phase_task_ids
        deps = [self.lead_task_id] + [
            mapping[dep]
            for dep in spec.depends_on
            if dep in mapping and mapping[dep] != self.lead_task_id
        ]
        return deps or [self.lead_task_id]

    def phase_task_id(self, phase_name: str) -> str:
        return self.phase_task_ids[phase_name]


def _coerce_role(value: Any) -> Role:
    raw = str(value or "").strip()
    if not raw:
        return Role.TEAM_LEAD
    try:
        return Role(raw.lower())
    except ValueError:
        return Role[raw.upper()]

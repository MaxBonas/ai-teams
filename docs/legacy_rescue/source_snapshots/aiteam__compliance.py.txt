from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompliancePolicy:
    environment: str = "dev"
    require_sensitive_approval: bool = True
    min_approvers_by_environment: dict[str, int] = field(
        default_factory=lambda: {"dev": 1, "stage": 1, "prod": 2}
    )
    sensitive_command_patterns: list[str] = field(
        default_factory=lambda: [
            r"\b(playstore|google\s+play|publish|release|deploy|production|prod)\b",
            r"\b(terraform\s+apply|kubectl\s+apply|docker\s+push)\b",
            r"\b(whatsapp|twilio|send\s+message)\b",
        ]
    )
    redaction_patterns: list[str] = field(
        default_factory=lambda: [
            # Key=value style secrets
            r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;\"']+)",
            r"(?i)(token\s*[:=]\s*)([^\s,;\"']+)",
            r"(?i)(secret\s*[:=]\s*)([^\s,;\"']+)",
            r"(?i)(password\s*[:=]\s*)([^\s,;\"']+)",
            r"(?i)(auth\s*[:=]\s*)([^\s,;\"']{8,})",
            r"(?i)(credentials?\s*[:=]\s*)([^\s,;\"']+)",
            # OpenAI / Anthropic / service key prefixes
            r"\b(sk-[A-Za-z0-9_\-]{12,})\b",
            r"\b(sk-ant-[A-Za-z0-9_\-]{12,})\b",
            r"\b(sk-proj-[A-Za-z0-9_\-]{12,})\b",
            # GitHub tokens
            r"\b(ghp_[A-Za-z0-9]{12,})\b",
            r"\b(github_pat_[A-Za-z0-9_]{12,})\b",
            r"\b(ghs_[A-Za-z0-9]{12,})\b",
            # Google / GCP
            r"\b(AIza[A-Za-z0-9_\-]{35})\b",
            # JWT tokens (3 base64 segments separated by dots)
            r"\b(eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)\b",
            # SSH private key markers
            r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)",
            # Webhook URLs with embedded tokens (e.g. Slack, Discord)
            r"(https://hooks\.slack\.com/services/[^\s\"']+)",
            r"(https://discord\.com/api/webhooks/[^\s\"']+)",
            # Generic long hex/base64 secrets (>=32 chars, not common words)
            r"(?<![A-Za-z0-9_\-])([A-Za-z0-9+/]{40,}={0,2})(?![A-Za-z0-9+/=])",
        ]
    )
    poisoning_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)(ignore\s+all\s+previous\s+instructions)",
            r"(?i)(system\s+override)",
            r"(?i)(disregard\s+all)",
            r"(?i)(you\s+are\s+now)",
            r"(?i)(new\s+persona)",
            r"(?i)(system\s+prompt\s*:)",
        ]
    )


class ComplianceGuard:
    def __init__(self, policy: CompliancePolicy | None = None, audit_trail: Any | None = None) -> None:
        self.policy = policy or CompliancePolicy()
        self.audit_trail = audit_trail
        self._compiled_sensitive = [
            re.compile(pattern, flags=re.IGNORECASE)
            for pattern in self.policy.sensitive_command_patterns
        ]
        self._compiled_redaction = [
            re.compile(pattern)
            for pattern in self.policy.redaction_patterns
        ]
        self._compiled_poisoning = [
            re.compile(pattern)
            for pattern in self.policy.poisoning_patterns
        ]

    def audit_decision(
        self,
        decision_type: str,
        task_id: str,
        approver_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
        rule_applied: str = "unknown"
    ) -> None:
        if self.audit_trail:
            self.audit_trail.audit_decision(
                decision_type=decision_type,
                task_id=task_id,
                approver_id=approver_id,
                reason=reason,
                metadata=metadata or {},
                rule_applied=rule_applied
            )

    def required_approvals(self) -> int:
        env = self.policy.environment.strip().lower()
        return max(1, int(self.policy.min_approvers_by_environment.get(env, 1)))

    def evaluate_sensitive_approval(self, task_metadata: dict[str, Any]) -> tuple[bool, str]:
        task_id = str(task_metadata.get("task_id", "unknown"))
        if not self.policy.require_sensitive_approval:
            self.audit_decision("approval_granted", task_id, "system", "approval_disabled", task_metadata, "policy.require_sensitive_approval=False")
            return True, "approval_disabled"

        approved_flag = _to_bool(task_metadata.get("approved_sensitive_ops", False))
        approvers = self._approved_by(task_metadata)
        required = self.required_approvals()
        
        main_approver = approvers[-1] if approvers else "unknown"

        if len(approvers) >= required:
            self.audit_decision("approval_granted", task_id, main_approver, f"approved by {approvers}", task_metadata, f"required_approvers={required}")
            return True, "approved"
        if approved_flag and required <= 1:
            self.audit_decision("approval_granted", task_id, main_approver, "approved flag true", task_metadata, f"required_approvers=1")
            return True, "approved"
        if approved_flag:
            self.audit_decision("approval_denied", task_id, main_approver, f"insufficient_approvers_required_{required}", task_metadata, f"required_approvers={required}")
            return False, f"insufficient_approvers_required_{required}"
        
        self.audit_decision("approval_denied", task_id, main_approver, "sensitive_commands_require_approval", task_metadata, "missing_approval_flag")
        return False, "sensitive_commands_require_approval"

    def approved_adapters(self, task_metadata: dict[str, Any]) -> set[str]:
        approved, _ = self.evaluate_sensitive_approval(task_metadata)
        if not approved:
            return set()
        raw = task_metadata.get("approved_adapters", [])
        if not isinstance(raw, list):
            return set()
        return {str(name).strip() for name in raw if str(name).strip()}

    def redact_text(self, value: str) -> str:
        if not value:
            return value
        redacted = value
        for pattern in self._compiled_redaction:
            redacted = pattern.sub(self._redact_match, redacted)
        return redacted

    def sanitize_context(self, text: str) -> str:
        """Elimina vectores de context poisoning de contextos externos."""
        if not text:
            return text
        sanitized = text
        for pattern in self._compiled_poisoning:
            sanitized = pattern.sub("<poisoning_attempt_redacted>", sanitized)
        return self.redact_text(sanitized)

    def validate_execution_plan(
        self,
        plan: list[dict],
        task_metadata: dict,
    ) -> tuple[bool, str, list[str]]:
        sensitive_commands: list[str] = []
        for step in plan:
            if not isinstance(step, dict):
                continue
            step_type = str(step.get("type", "")).strip().lower()
            if step_type not in {"cmd", "powershell"}:
                continue
            command = str(step.get("command", "")).strip()
            if not command:
                continue
            if self.is_sensitive_command(command):
                sensitive_commands.append(command)

        if not sensitive_commands:
            return True, "ok", []

        if not self.policy.require_sensitive_approval:
            return True, "approval_disabled", sensitive_commands

        approved, reason = self.evaluate_sensitive_approval(task_metadata)
        if approved:
            return True, "approved", sensitive_commands

        return False, reason, sensitive_commands

    def is_sensitive_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        for pattern in self._compiled_sensitive:
            if pattern.search(normalized):
                return True
        return False

    @staticmethod
    def _redact_match(match: re.Match[str]) -> str:
        if match.lastindex and match.lastindex >= 2:
            return f"{match.group(1)}<redacted>"
        return "<redacted>"

    @staticmethod
    def _approved_by(task_metadata: dict[str, Any]) -> list[str]:
        raw = task_metadata.get("approved_by", [])
        if not isinstance(raw, list):
            return []
        items = []
        for item in raw:
            text = str(item).strip()
            if text:
                items.append(text)
        return items


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}

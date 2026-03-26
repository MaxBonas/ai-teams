from __future__ import annotations

import json
from pathlib import Path

from aiteam.adapters.api import ApiAdapter
from aiteam.adapters.base import ModelAdapter
from aiteam.adapters.external_program import ExternalProgramAdapter
from aiteam.adapters.subscription import SubscriptionAdapter
from aiteam.types import ChannelType


def load_external_adapters(config_path: Path) -> list[ModelAdapter]:
    if not config_path.exists():
        return []
    raw = config_path.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = payload.get("external_adapters", [])
    if not isinstance(items, list):
        return []

    adapters: list[ModelAdapter] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not _is_enabled(item.get("enabled", True)):
            continue
        adapter = _build_adapter(item)
        if adapter is not None:
            adapters.append(adapter)
    return adapters


def build_external_adapter_template(config_path: Path) -> None:
    template = {
        "external_adapters": [
            {
                "type": "external_program",
                "name": "my_engineer_runtime",
                "provider": "custom",
                "model": "runtime-v1",
                "channel": "subscription",
                "command": ["python", "-c", "print('external runtime ok')"],
                "capabilities": ["coding", "analysis"],
                "role_targets": ["engineer", "reviewer"],
                "priority": "secondary",
                "enabled": True,
                "requires_approval": False,
                "timeout_seconds": 120,
                "cost_tier": 0,
            },
            {
                "type": "external_program",
                "name": "secretariawhatsapp",
                "provider": "custom",
                "model": "whatsapp-assistant",
                "channel": "subscription",
                "command": ["python", "-c", "print('configure secretariawhatsapp command')"],
                "capabilities": ["analysis", "knowledge_base", "messaging"],
                "role_targets": ["team_lead", "researcher"],
                "priority": "secondary",
                "enabled": False,
                "requires_approval": True,
                "timeout_seconds": 120,
                "cost_tier": 1,
            },
            {
                "type": "external_program",
                "name": "playstore_publisher",
                "provider": "custom",
                "model": "android-release-bot",
                "channel": "subscription",
                "command": ["python", "-c", "print('configure playstore publisher command')"],
                "capabilities": ["release", "android", "automation"],
                "role_targets": ["engineer", "qa"],
                "priority": "secondary",
                "enabled": False,
                "requires_approval": True,
                "timeout_seconds": 180,
                "cost_tier": 1,
            },
            {
                "type": "external_program",
                "name": "android_browser_auditor",
                "provider": "custom",
                "model": "android-audit-bot",
                "channel": "subscription",
                "command": ["python", "-c", "print('configure android auditor command')"],
                "capabilities": ["qa", "android", "browser_testing"],
                "role_targets": ["qa"],
                "priority": "secondary",
                "enabled": False,
                "requires_approval": True,
                "timeout_seconds": 180,
                "cost_tier": 1,
            },
            {
                "type": "external_program",
                "name": "video_editor_remotion",
                "provider": "custom",
                "model": "remotion-video-suite",
                "channel": "subscription",
                "command": [
                    "cmd",
                    "/c",
                    "cd /d \"C:\\Users\\Max\\Antigravity Projects\\VideoGenerator\" && npm run build -- --help",
                ],
                "capabilities": ["multimodal", "video_generation", "rendering"],
                "role_targets": ["engineer", "researcher"],
                "priority": "secondary",
                "enabled": False,
                "requires_approval": False,
                "timeout_seconds": 180,
                "cost_tier": 1,
            },
            {
                "type": "external_program",
                "name": "notebooklm_bridge",
                "provider": "notebooklm",
                "model": "knowledge-sync-v1",
                "channel": "subscription",
                "command": [
                    "python",
                    "-m",
                    "aiteam.cli",
                    "notebooklm-sync",
                    "--runtime-dir",
                    "runtime",
                    "--notebooklm-from-prompt",
                    "{prompt}",
                    "--quiet",
                ],
                "capabilities": ["knowledge_base", "learning_sync", "summarization"],
                "role_targets": ["team_lead", "researcher"],
                "priority": "secondary",
                "enabled": False,
                "requires_approval": False,
                "timeout_seconds": 180,
                "cost_tier": 0,
            },
        ]
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(template, indent=2), encoding="utf-8")


def _build_adapter(item: dict) -> ModelAdapter | None:
    kind = str(item.get("type", "external_program")).strip().lower()
    name = str(item.get("name", "external")).strip()
    provider = str(item.get("provider", "custom")).strip().lower()
    model = str(item.get("model", "unknown")).strip()
    capabilities = _to_str_set(item.get("capabilities", []))
    role_targets = _to_str_set(item.get("role_targets", []))
    cost_tier = int(item.get("cost_tier", 1))
    channel = _to_channel(item.get("channel", "subscription"))
    routing_priority = _resolve_routing_priority(item, kind)
    requires_approval = _to_bool(item.get("requires_approval", False))

    if kind == "external_program":
        command = item.get("command", [])
        if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
            return None
        return ExternalProgramAdapter(
            name=name,
            provider=provider,
            model=model,
            command=command,
            capabilities=capabilities,
            channel=channel,
            timeout_seconds=int(item.get("timeout_seconds", 120)),
            cost_tier=cost_tier,
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )

    if kind == "subscription":
        return SubscriptionAdapter(
            name=name,
            provider=provider,
            model=model,
            capabilities=capabilities,
            cost_tier=cost_tier,
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )

    if kind == "api":
        return ApiAdapter(
            name=name,
            provider=provider,
            model=model,
            capabilities=capabilities,
            cost_tier=cost_tier,
            require_key=_to_bool(item.get("require_key", False)),
            role_targets=role_targets,
            routing_priority=routing_priority,
            requires_approval=requires_approval,
        )

    return None


def _to_channel(value: object) -> ChannelType:
    try:
        return ChannelType(str(value).strip().lower())
    except ValueError:
        return ChannelType.SUBSCRIPTION


def _to_str_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _resolve_routing_priority(item: dict, kind: str) -> int:
    explicit = item.get("routing_priority")
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass

    priority = str(item.get("priority", "")).strip().lower()
    if priority == "primary":
        return 100
    if priority == "secondary":
        return 200

    if kind == "external_program":
        return 200
    return 100


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized not in {"0", "false", "no", "off", ""}


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}

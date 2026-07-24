from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from aiteam.platform_runtime import platform_id, resolve_executable
from aiteam.policies import WORKSPACE_NOISE_DIRS


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "ecosystem_registry_v1"
PROJECTION_VERSION = "project_toolchain_projection_v1"
CAPABILITY_GAP_VERSION = "capability_gap_v1"
DEFAULT_REGISTRY_PATH = ROOT / "config" / "ecosystems.v1.json"
DEFAULT_SCHEMA_PATH = ROOT / "config" / "ecosystem_registry.v1.schema.json"
ACTION_IDS = ("configure", "build", "test", "lint", "typecheck")
ECOSYSTEM_IDS = (
    "python",
    "javascript_typescript",
    "java_kotlin",
    "go",
    "rust",
    "c_cpp",
    "dotnet",
    "php",
    "ruby",
    "swift",
    "web_mobile",
    "containers_devcontainers",
)
_ALLOWED_COMMAND_STATUS = {"legacy_enabled", "planned"}
_ROLE_ACTIONS = {
    "lead": (),
    "team_lead": (),
    "architect": (),
    "engineer": ACTION_IDS,
    "reviewer": ("lint", "typecheck"),
    "qa": ("test",),
    "test_designer": ("test",),
    "test_runner": ("test",),
}


def load_ecosystem_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_ecosystem_registry(payload)
    return payload


def load_ecosystem_schema(path: Path | None = None) -> dict[str, Any]:
    schema = json.loads((path or DEFAULT_SCHEMA_PATH).read_text(encoding="utf-8"))
    if schema.get("title") != SCHEMA_VERSION:
        raise ValueError("unsupported ecosystem registry schema")
    if schema.get("additionalProperties") is not False:
        raise ValueError("ecosystem registry schema must fail closed")
    return schema


def validate_ecosystem_registry(payload: Mapping[str, Any]) -> None:
    expected_top = {
        "schema_version",
        "contract_status",
        "action_ids",
        "execution_policy",
        "ecosystems",
        "known_gaps",
    }
    if set(payload) != expected_top:
        raise ValueError("ecosystem registry fields drift")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported ecosystem registry")
    if tuple(payload.get("action_ids") or ()) != ACTION_IDS:
        raise ValueError("ecosystem action coverage drift")
    policy = payload.get("execution_policy")
    if not isinstance(policy, Mapping) or policy.get("detection_mutates") is not False:
        raise ValueError("ecosystem detection must be read-only")
    if policy.get("automatic_install") is not False:
        raise ValueError("ecosystem registry cannot install dependencies")
    if policy.get("descriptor_only_commands") is not True:
        raise ValueError("ecosystem commands must be descriptor-bound")
    if policy.get("requires_authorization") is not True:
        raise ValueError("ecosystem execution must require authorization")
    if policy.get("cwd_within_workspace") is not True:
        raise ValueError("ecosystem cwd must stay inside workspace")
    allowed_env = set(policy.get("allowed_env") or ())
    max_timeout = int(policy.get("max_timeout_seconds") or 0)
    if not allowed_env or max_timeout <= 0:
        raise ValueError("ecosystem execution policy incomplete")

    ecosystems = payload.get("ecosystems")
    if not isinstance(ecosystems, list):
        raise ValueError("ecosystem registry entries must be a list")
    ids = tuple(str(item.get("id") or "") for item in ecosystems)
    if ids != ECOSYSTEM_IDS:
        raise ValueError("ecosystem registry order or coverage drift")
    for ecosystem in ecosystems:
        _validate_ecosystem(ecosystem, allowed_env=allowed_env, max_timeout=max_timeout)
    gaps = payload.get("known_gaps")
    if not isinstance(gaps, list) or not gaps or any(not str(item).strip() for item in gaps):
        raise ValueError("ecosystem registry known gaps required")


def _validate_ecosystem(
    ecosystem: Mapping[str, Any],
    *,
    allowed_env: set[str],
    max_timeout: int,
) -> None:
    expected = {
        "id",
        "label",
        "status",
        "categories",
        "detectors",
        "runtimes",
        "action_dependencies",
        "commands",
        "artifacts",
    }
    if set(ecosystem) != expected:
        raise ValueError(f"ecosystem fields drift:{ecosystem.get('id')}")
    detector = ecosystem.get("detectors")
    if not isinstance(detector, Mapping) or set(detector) != {"manifests", "extensions"}:
        raise ValueError(f"ecosystem detectors invalid:{ecosystem.get('id')}")
    runtime_ids: set[str] = set()
    for runtime in ecosystem.get("runtimes") or ():
        if set(runtime) != {"id", "candidates", "version_args"}:
            raise ValueError(f"ecosystem runtime fields drift:{ecosystem.get('id')}")
        runtime_id = str(runtime.get("id") or "")
        if not runtime_id or runtime_id in runtime_ids:
            raise ValueError(f"ecosystem runtime duplicate:{ecosystem.get('id')}")
        runtime_ids.add(runtime_id)
    dependencies = ecosystem.get("action_dependencies")
    if not isinstance(dependencies, list):
        raise ValueError(f"ecosystem action dependencies invalid:{ecosystem.get('id')}")
    dependency_actions: set[str] = set()
    dependency_graph: dict[str, set[str]] = {}
    for dependency in dependencies:
        if set(dependency) != {"action", "requires"}:
            raise ValueError(
                f"ecosystem action dependency fields drift:{ecosystem.get('id')}"
            )
        action = str(dependency.get("action") or "")
        requires = {str(item) for item in dependency.get("requires") or ()}
        if (
            action not in ACTION_IDS
            or action in dependency_actions
            or not requires
            or not requires.issubset(ACTION_IDS)
            or action in requires
        ):
            raise ValueError(
                f"ecosystem action dependency invalid:{ecosystem.get('id')}:{action}"
            )
        dependency_actions.add(action)
        dependency_graph[action] = requires
    _validate_action_dependency_graph(
        dependency_graph,
        ecosystem_id=str(ecosystem.get("id") or ""),
    )
    commands = ecosystem.get("commands")
    if not isinstance(commands, Mapping) or set(commands) != set(ACTION_IDS):
        raise ValueError(f"ecosystem command coverage drift:{ecosystem.get('id')}")
    command_ids: set[str] = set()
    expected_command_fields = {
        "id",
        "runtime_id",
        "argv",
        "selectors",
        "cwd",
        "env",
        "timeout_seconds",
        "required_capability",
        "mutates_workspace",
        "project_script",
        "status",
    }
    for action_id in ACTION_IDS:
        entries = commands.get(action_id)
        if not isinstance(entries, list):
            raise ValueError(f"ecosystem command list invalid:{ecosystem.get('id')}:{action_id}")
        for command in entries:
            if set(command) != expected_command_fields:
                raise ValueError(
                    f"ecosystem command fields drift:{ecosystem.get('id')}:{action_id}"
                )
            command_id = str(command.get("id") or "")
            if not command_id or command_id in command_ids:
                raise ValueError(f"ecosystem command duplicate:{ecosystem.get('id')}")
            command_ids.add(command_id)
            if str(command.get("runtime_id") or "") not in runtime_ids:
                raise ValueError(f"ecosystem command runtime missing:{command_id}")
            if command.get("status") not in _ALLOWED_COMMAND_STATUS:
                raise ValueError(f"ecosystem command status invalid:{command_id}")
            if not set(command.get("env") or ()).issubset(allowed_env):
                raise ValueError(f"ecosystem command env denied:{command_id}")
            timeout = int(command.get("timeout_seconds") or 0)
            if timeout <= 0 or timeout > max_timeout:
                raise ValueError(f"ecosystem command timeout invalid:{command_id}")
            argv = command.get("argv")
            if not isinstance(argv, list) or not argv or argv[0] != "{runtime}":
                raise ValueError(f"ecosystem command argv invalid:{command_id}")
            if command.get("cwd") not in {"workspace", "manifest_dir"}:
                raise ValueError(f"ecosystem command cwd invalid:{command_id}")
            selectors = command.get("selectors")
            if not isinstance(selectors, Mapping) or set(selectors) != {
                "manifests",
                "globs",
                "json_script",
                "content_markers",
            }:
                raise ValueError(f"ecosystem command selectors invalid:{command_id}")


def _validate_action_dependency_graph(
    graph: Mapping[str, set[str]],
    *,
    ecosystem_id: str,
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action: str) -> None:
        if action in visiting:
            raise ValueError(f"ecosystem action dependency cycle:{ecosystem_id}")
        if action in visited:
            return
        visiting.add(action)
        for requirement in graph.get(action, set()):
            visit(requirement)
        visiting.remove(action)
        visited.add(action)

    for action in graph:
        visit(action)


def detect_project_ecosystems(
    root: Path,
    *,
    registry: Mapping[str, Any] | None = None,
    max_files: int = 20_000,
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    payload = dict(registry or load_ecosystem_registry())
    validate_ecosystem_registry(payload)
    rel_paths, truncated = _bounded_workspace_paths(workspace, max_files=max_files)
    observations: list[dict[str, Any]] = []
    for descriptor in payload["ecosystems"]:
        detector = descriptor["detectors"]
        manifests = [
            rel for rel in rel_paths if _matches_any(rel, detector["manifests"])
        ]
        extensions = set(detector["extensions"])
        extension_hits = [
            rel for rel in rel_paths if Path(rel).suffix.lower() in extensions
        ]
        detected = bool(manifests or extension_hits)
        if not detected:
            continue
        observations.append(
            {
                "id": descriptor["id"],
                "label": descriptor["label"],
                "status": descriptor["status"],
                "categories": list(descriptor["categories"]),
                "manifests": manifests[:50],
                "extension_count": len(extension_hits),
                "extension_samples": extension_hits[:20],
                "available_actions": [
                    action_id
                    for action_id in ACTION_IDS
                    if _applicable_commands(
                        workspace,
                        rel_paths,
                        descriptor,
                        action_id,
                    )
                ],
                "support_claim": False,
                "source": SCHEMA_VERSION,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_observed": workspace.is_dir(),
        "scan_truncated": truncated,
        "files_observed": len(rel_paths),
        "ecosystems": observations,
        "detected_ids": [item["id"] for item in observations],
        "support_claims": [],
        "commands_executed": False,
        "installation_performed": False,
        "mutated": False,
    }


def plan_ecosystem_command(
    root: Path,
    *,
    ecosystem_id: str,
    action_id: str,
    granted_capabilities: Iterable[str] = (),
    authorized: bool = False,
    include_planned: bool = False,
    completed_actions: Iterable[str] = (),
    runtime_overrides: Mapping[str, str] | None = None,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    payload = dict(registry or load_ecosystem_registry())
    validate_ecosystem_registry(payload)
    if action_id not in ACTION_IDS:
        return _denied_plan(ecosystem_id, action_id, "unsupported_action")
    descriptor = next(
        (item for item in payload["ecosystems"] if item["id"] == ecosystem_id),
        None,
    )
    if descriptor is None:
        return _denied_plan(ecosystem_id, action_id, "unknown_ecosystem")
    rel_paths, _ = _bounded_workspace_paths(workspace, max_files=20_000)
    command = next(
        (
            item
            for item in descriptor["commands"][action_id]
            if _selectors_match(workspace, rel_paths, item["selectors"])
        ),
        None,
    )
    if command is None:
        return _denied_plan(ecosystem_id, action_id, "no_applicable_descriptor")
    capability = str(command["required_capability"])
    if capability not in set(granted_capabilities):
        return _denied_plan(
            ecosystem_id,
            action_id,
            f"capability_not_granted:{capability}",
            command_id=command["id"],
        )
    if not authorized:
        return _denied_plan(
            ecosystem_id,
            action_id,
            "execution_not_authorized",
            command_id=command["id"],
        )
    if command["status"] == "planned" and not include_planned:
        return _denied_plan(
            ecosystem_id,
            action_id,
            "verification_required",
            command_id=command["id"],
        )
    requires_actions = next(
        (
            tuple(str(item) for item in dependency["requires"])
            for dependency in descriptor["action_dependencies"]
            if dependency["action"] == action_id
        ),
        (),
    )
    missing_actions = sorted(set(requires_actions) - set(completed_actions))
    if missing_actions:
        return _denied_plan(
            ecosystem_id,
            action_id,
            f"prerequisite_not_satisfied:{','.join(missing_actions)}",
            command_id=command["id"],
        )
    runtime = _resolve_runtime(
        descriptor,
        str(command["runtime_id"]),
        overrides=runtime_overrides or {},
    )
    if runtime is None:
        return _denied_plan(
            ecosystem_id,
            action_id,
            f"runtime_unavailable:{command['runtime_id']}",
            command_id=command["id"],
        )
    cwd = _command_cwd(workspace, rel_paths, command)
    if cwd is None:
        return _denied_plan(
            ecosystem_id,
            action_id,
            "command_cwd_unresolved",
            command_id=command["id"],
        )
    test_targets = sorted(
        rel
        for rel in rel_paths
        if _matches_any(rel, command["selectors"]["globs"])
    )
    argv: list[str] = []
    for item in command["argv"]:
        if item == "{runtime}":
            argv.append(runtime)
        elif item == "{test_targets}":
            argv.extend(test_targets)
        else:
            argv.append(str(item))
    return {
        "schema_version": "ecosystem_command_plan_v1",
        "allowed": True,
        "reason": "descriptor_authorized",
        "ecosystem_id": ecosystem_id,
        "action_id": action_id,
        "command_id": command["id"],
        "runtime_id": command["runtime_id"],
        "runtime_version_args": list(
            next(
                item["version_args"]
                for item in descriptor["runtimes"]
                if item["id"] == command["runtime_id"]
            )
        ),
        "requires_actions": list(requires_actions),
        "argv": argv,
        "cwd": "." if cwd == workspace else cwd.relative_to(workspace).as_posix(),
        "env": dict(command["env"]),
        "timeout_seconds": int(command["timeout_seconds"]),
        "required_capability": capability,
        "mutates_workspace": bool(command["mutates_workspace"]),
        "project_script": bool(command["project_script"]),
        "support_claim": False,
        "source": SCHEMA_VERSION,
    }


def project_toolchain_projection(
    root: Path,
    *,
    role: str,
    granted_capabilities: Iterable[str] = (),
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    detection = detect_project_ecosystems(workspace, registry=registry)
    role_key = str(role or "").strip().lower()
    permitted_actions = _ROLE_ACTIONS.get(role_key, ())
    granted = set(granted_capabilities)
    capability_gaps: set[str] = set()
    hiring: dict[str, set[str]] = {}
    ecosystems: list[dict[str, Any]] = []
    payload = dict(registry or load_ecosystem_registry())
    by_id = {item["id"]: item for item in payload["ecosystems"]}
    rel_paths, _ = _bounded_workspace_paths(workspace, max_files=20_000)
    for observed in detection["ecosystems"]:
        descriptor = by_id[observed["id"]]
        action_contracts = []
        for action_id in ACTION_IDS:
            commands = _applicable_commands(
                workspace,
                rel_paths,
                descriptor,
                action_id,
            )
            if not commands:
                continue
            required = {str(item["required_capability"]) for item in commands}
            owner_role = "test_runner" if action_id == "test" else "engineer"
            hiring.setdefault(owner_role, set()).update(required)
        for action_id in permitted_actions:
            commands = _applicable_commands(
                workspace,
                rel_paths,
                descriptor,
                action_id,
            )
            if not commands:
                continue
            required = sorted({str(item["required_capability"]) for item in commands})
            capability_gaps.update(item for item in required if item not in granted)
            action_contracts.append(
                {
                    "action": action_id,
                    "required_capabilities": required,
                    "command_ids": [str(item["id"]) for item in commands],
                    "execution_authorized": False,
                }
            )
        ecosystems.append(
            {
                **observed,
                "action_contracts": action_contracts,
            }
        )
    return {
        "schema_version": PROJECTION_VERSION,
        "role": role_key,
        "ecosystems": ecosystems,
        "detected_ids": detection["detected_ids"],
        "capability_gaps": sorted(capability_gaps),
        "hiring_requirements": [
            {
                "role": owner_role,
                "capabilities": sorted(capabilities),
                "reason": "detected_ecosystem_action",
            }
            for owner_role, capabilities in sorted(hiring.items())
        ],
        "commands_executed": False,
        "installation_performed": False,
        "support_claim": False,
    }


def doctor_probe_specs(
    registry: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    payload = dict(registry or load_ecosystem_registry())
    specs = []
    for ecosystem in payload["ecosystems"]:
        runtimes = ecosystem["runtimes"]
        primary = runtimes[0] if runtimes else None
        specs.append(
            {
                "id": ecosystem["id"],
                "runtime_candidates": tuple(primary["candidates"]) if primary else (),
                "version_args": tuple(primary["version_args"]) if primary else (),
            }
        )
    return tuple(specs)


def _denied_plan(
    ecosystem_id: str,
    action_id: str,
    reason: str,
    *,
    command_id: str | None = None,
) -> dict[str, Any]:
    owner, next_action = _gap_remediation(reason)
    return {
        "schema_version": "ecosystem_command_plan_v1",
        "allowed": False,
        "reason": reason,
        "ecosystem_id": ecosystem_id,
        "action_id": action_id,
        "command_id": command_id,
        "capability_gap": {
            "schema_version": CAPABILITY_GAP_VERSION,
            "ecosystem_id": ecosystem_id,
            "action_id": action_id,
            "descriptor_id": command_id,
            "owner": owner,
            "reason": reason,
            "next_action": next_action,
        },
        "support_claim": False,
        "source": SCHEMA_VERSION,
    }


def _gap_remediation(reason: str) -> tuple[str, str]:
    if reason.startswith("runtime_unavailable:"):
        return (
            "machine_environment_owner",
            "Configure the descriptor runtime outside AI Teams and rerun validation.",
        )
    if reason.startswith("capability_not_granted:"):
        return (
            "team_owner",
            "Assign the required capability to an eligible role; do not bypass RBAC.",
        )
    if reason == "execution_not_authorized":
        return (
            "project_owner",
            "Authorize this descriptor-bound action explicitly.",
        )
    if reason == "verification_required":
        return (
            "ecosystem_registry_owner",
            "Run the versioned fixture matrix and review its receipt before promotion.",
        )
    if reason == "command_cwd_unresolved":
        return (
            "project_owner",
            "Provide the expected manifest inside the workspace.",
        )
    if reason.startswith("prerequisite_not_satisfied:"):
        return (
            "workflow_owner",
            "Complete and record the required descriptor actions before this action.",
        )
    return (
        "ecosystem_registry_owner",
        "Add or correct a versioned descriptor and fixture before execution.",
    )


def _bounded_workspace_paths(
    workspace: Path,
    *,
    max_files: int,
) -> tuple[tuple[str, ...], bool]:
    """Enumera archivos sin seguir enlaces ni atravesar ruido, con límite real."""
    if max_files <= 0 or not workspace.is_dir():
        return (), False
    paths: list[str] = []
    truncated = False
    for current, dirnames, filenames in os.walk(
        workspace,
        topdown=True,
        followlinks=False,
    ):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in WORKSPACE_NOISE_DIRS
            and not (Path(current) / name).is_symlink()
        )
        for filename in sorted(filenames):
            candidate = Path(current) / filename
            if candidate.is_symlink():
                continue
            try:
                relative = candidate.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if len(paths) >= max_files:
                truncated = True
                return tuple(paths), truncated
            paths.append(relative)
    return tuple(paths), truncated


def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    rel = rel_path.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        normalized = str(pattern).replace("\\", "/")
        if "/" in normalized:
            if fnmatch.fnmatchcase(rel, normalized) or rel.endswith("/" + normalized):
                return True
        elif fnmatch.fnmatchcase(name, normalized):
            return True
    return False


def _selectors_match(
    root: Path,
    rel_paths: tuple[str, ...],
    selectors: Mapping[str, Any],
) -> bool:
    if selectors["manifests"] and any(
        _matches_any(rel, selectors["manifests"]) for rel in rel_paths
    ):
        return True
    if selectors["globs"] and any(
        _matches_any(rel, selectors["globs"]) for rel in rel_paths
    ):
        return True
    json_script = selectors.get("json_script")
    if json_script:
        for rel in rel_paths:
            if not _matches_any(rel, ("package.json",)):
                continue
            try:
                package = json.loads((root / rel).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            script = str((package.get("scripts") or {}).get(str(json_script)) or "")
            if script and "no test specified" not in script:
                return True
    for manifest, markers in selectors["content_markers"].items():
        for rel in rel_paths:
            if not _matches_any(rel, (manifest,)):
                continue
            try:
                content = (root / rel).read_text(
                    encoding="utf-8", errors="ignore"
                ).casefold()[:256_000]
            except OSError:
                continue
            if any(str(marker).casefold() in content for marker in markers):
                return True
    return False


def _applicable_commands(
    root: Path,
    rel_paths: tuple[str, ...],
    descriptor: Mapping[str, Any],
    action_id: str,
) -> list[Mapping[str, Any]]:
    return [
        command
        for command in descriptor["commands"][action_id]
        if _selectors_match(root, rel_paths, command["selectors"])
    ]


def _resolve_runtime(
    descriptor: Mapping[str, Any],
    runtime_id: str,
    *,
    overrides: Mapping[str, str],
) -> str | None:
    override = str(overrides.get(runtime_id) or "").strip()
    if override:
        return override if Path(override).is_file() else None
    runtime = next(
        (item for item in descriptor["runtimes"] if item["id"] == runtime_id),
        None,
    )
    if runtime is None:
        return None
    for candidate in runtime["candidates"]:
        resolved = resolve_executable(str(candidate), os_id=platform_id())
        if resolved:
            return resolved
    return None


def _command_cwd(
    workspace: Path,
    rel_paths: tuple[str, ...],
    command: Mapping[str, Any],
) -> Path | None:
    if command["cwd"] == "workspace":
        return workspace
    selectors = command["selectors"]
    manifests = selectors["manifests"]
    if not manifests and selectors.get("json_script"):
        manifests = ("package.json",)
    match = next((rel for rel in rel_paths if _matches_any(rel, manifests)), None)
    if not match:
        return None
    candidate = (workspace / match).parent.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    if any(part in WORKSPACE_NOISE_DIRS for part in candidate.relative_to(workspace).parts):
        return None
    return candidate

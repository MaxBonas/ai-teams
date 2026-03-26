from __future__ import annotations

import json
import os
import re
import hashlib
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolIntegrationReport:
    success: bool = True
    integrated_adapters: list[str] = field(default_factory=list)
    integrated_skills: list[str] = field(default_factory=list)
    integrated_mcp_servers: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AutoToolIntegrator:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        project_root: Path,
        catalog_path: Path | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir.resolve()
        self.project_root = project_root.resolve()
        self.catalog_path = (catalog_path or self.project_root / "config" / "tool_sources.catalog.json").resolve()
        self.skills_library_path = (self.project_root / "config" / "skills.library.json").resolve()
        self.skills_sources_path = (self.project_root / "config" / "skills.sources.json").resolve()
        self.adapters_path = self.runtime_dir / "adapters.json"
        self.mcp_path = self.runtime_dir / "mcp_servers.json"
        self.registry_path = self.runtime_dir / "tool_registry.json"
        self.skills_registry_path = self.runtime_dir / "skills_registry.json"
        self.skills_root = self.project_root / ".cloud" / "skills"
        self.skills_root_agents = self.project_root / ".agents" / "skills"
        self.skills_root_claude = self.project_root / ".claude" / "skills"
        
        from aiteam.tool_lock import ToolLockManager
        self.tool_lock = ToolLockManager(self.runtime_dir)

    def skill_library_entries(self) -> list[dict[str, Any]]:
        payload = _load_json(self.skills_library_path, default={"skills": []})
        items = payload.get("skills", [])
        output_by_name: dict[str, dict[str, Any]] = {}

        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip().lower()
                if not name:
                    continue
                output_by_name[name] = dict(item)

        for item in self.skill_registry_entries():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().lower()
            if not name:
                continue
            if name not in output_by_name:
                output_by_name[name] = dict(item)

        return list(output_by_name.values())

    def skill_registry_entries(self) -> list[dict[str, Any]]:
        payload = _load_json(self.skills_registry_path, default={"skills": []})
        items = payload.get("skills", [])
        if not isinstance(items, list):
            return []
        output: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                output.append(item)
        return output

    def sync_skill_library(
        self,
        force: bool = False,
        targets: set[str] | None = None,
    ) -> list[str]:
        selected_targets = _normalize_skill_targets(targets)
        created: list[str] = []
        for skill in self.skill_library_entries():
            name = str(skill.get("name", "")).strip().lower()
            if not name:
                continue
            slug = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-") or "skill"
            canonical = self._canonical_skill_content(skill)
            changed = self._write_skill_to_targets(
                slug=slug,
                canonical_content=canonical,
                legacy_content=self._legacy_skill_content(skill),
                force=force,
                targets=selected_targets,
            )
            if changed:
                created.append(name)
        return created

    def pull_skill_sources(
        self,
        *,
        batch: str = "",
        force: bool = False,
        max_items: int = 0,
        targets: set[str] | None = None,
    ) -> dict[str, Any]:
        payload = _load_json(self.skills_sources_path, default={"policy": {}, "sources": []})
        policy = payload.get("policy", {})
        policy_dict = policy if isinstance(policy, dict) else {}
        source_items = payload.get("sources", [])
        if not isinstance(source_items, list):
            source_items = []

        selected_batches = {
            item.strip().lower() for item in str(batch).split(",") if item.strip()
        }
        selected_targets = _normalize_skill_targets(targets)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            max_skill_chars = int(policy_dict.get("max_skill_chars", 300000))
        except (TypeError, ValueError):
            max_skill_chars = 300000
        max_skill_chars = max(1000, max_skill_chars)
        require_sha256_pin = _to_bool(policy_dict.get("require_sha256_pin", False))
        enforce_item_sha256_if_present = _to_bool(
            policy_dict.get("enforce_item_sha256_if_present", True)
        )
        allowed_license_keywords = _normalize_string_list(
            policy_dict.get("allowed_license_keywords", [])
        )
        require_license_match = _to_bool(
            policy_dict.get("require_license_match", False)
        )

        existing: dict[str, dict[str, Any]] = {}
        for item in self.skill_registry_entries():
            name = str(item.get("name", "")).strip().lower()
            if name:
                existing[name] = dict(item)

        pulled = 0
        skipped = 0
        errors: list[str] = []
        warnings: list[str] = []
        changed: list[str] = []

        for source in source_items:
            if not isinstance(source, dict):
                continue
            source_name = str(source.get("name", "")).strip().lower()
            if not source_name:
                continue
            if selected_batches and source_name not in selected_batches:
                continue
            if not _to_bool(source.get("enabled", True)) and not force:
                skipped += 1
                continue

            repo = str(source.get("repo", "")).strip().lower()
            if not _repo_is_allowed(repo=repo, policy=policy_dict):
                errors.append(f"source_repo_not_allowed:{source_name}:{repo}")
                continue

            branch = str(source.get("branch", "")).strip() or str(
                policy_dict.get("default_branch", "main")
            ).strip() or "main"

            skills = source.get("skills", [])
            if not isinstance(skills, list):
                skills = []

            for skill_item in skills:
                if max_items > 0 and pulled >= max_items:
                    break
                if not isinstance(skill_item, dict):
                    continue

                skill_path = str(skill_item.get("path", "")).strip().lstrip("/")
                if not skill_path:
                    continue
                if not skill_path.lower().endswith("skill.md"):
                    skill_path = f"{skill_path.rstrip('/')}/SKILL.md"

                raw_url = (
                    f"https://raw.githubusercontent.com/{repo}/{branch}/{skill_path}"
                )
                try:
                    with urllib.request.urlopen(raw_url, timeout=25) as response:
                        content = response.read().decode("utf-8", errors="replace")
                except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                    errors.append(f"skill_pull_failed:{source_name}:{skill_path}:{exc}")
                    continue

                content_clean = content.strip()
                if len(content_clean) > max_skill_chars:
                    errors.append(
                        f"skill_too_large:{source_name}:{skill_path}:chars={len(content_clean)}:max={max_skill_chars}"
                    )
                    continue

                expected_sha256 = str(skill_item.get("sha256", "")).strip().lower()
                if require_sha256_pin and not expected_sha256:
                    errors.append(f"skill_missing_sha256_pin:{source_name}:{skill_path}")
                    continue

                content_hash = hashlib.sha256(content_clean.encode("utf-8")).hexdigest()
                if expected_sha256 and content_hash != expected_sha256:
                    errors.append(
                        f"skill_sha256_mismatch:{source_name}:{skill_path}:expected={expected_sha256}:actual={content_hash}"
                    )
                    continue
                if (
                    not expected_sha256
                    and enforce_item_sha256_if_present
                    and require_sha256_pin
                ):
                    errors.append(f"skill_unpinned_denied:{source_name}:{skill_path}")
                    continue

                parsed = _extract_skill_frontmatter(content)
                license_text = str(parsed.get("license", "")).strip().lower()
                if allowed_license_keywords:
                    matched_license = any(
                        keyword and keyword in license_text
                        for keyword in allowed_license_keywords
                    )
                    if require_license_match and not matched_license:
                        errors.append(
                            f"skill_license_not_allowed:{source_name}:{skill_path}:license={license_text or 'missing'}"
                        )
                        continue
                    if not require_license_match and not matched_license:
                        warnings.append(
                            f"skill_license_unverified:{source_name}:{skill_path}:license={license_text or 'missing'}"
                        )

                parsed_name = str(parsed.get("name", "")).strip().lower()
                default_name = Path(skill_path).parent.name.strip().lower()
                raw_name = str(skill_item.get("name", parsed_name or default_name)).strip().lower()
                normalized_name = _safe_slug(raw_name)
                if not normalized_name:
                    errors.append(f"skill_name_invalid:{source_name}:{skill_path}")
                    continue

                resolved_name = normalized_name
                existing_same_name = existing.get(normalized_name)
                if isinstance(existing_same_name, dict):
                    existing_repo = str(existing_same_name.get("source_repo", "")).strip().lower()
                    existing_path = str(existing_same_name.get("source_path", "")).strip().lower()
                    if existing_repo and (existing_repo != repo or existing_path != skill_path.lower()):
                        provider_prefix = _safe_slug(source.get("provider", repo.split("/", 1)[0]))
                        candidate = _safe_slug(f"{provider_prefix}-{normalized_name}")
                        if candidate in existing and str(existing[candidate].get("source_repo", "")).strip().lower() != repo:
                            candidate = _safe_slug(f"{repo.split('/', 1)[0]}-{normalized_name}")
                        resolved_name = candidate

                if (
                    resolved_name in existing
                    and not force
                    and str(existing[resolved_name].get("content_sha256", "")).strip() == content_hash
                ):
                    skipped += 1
                    continue

                description = str(
                    skill_item.get("description")
                    or skill_item.get("purpose")
                    or parsed.get("description", "")
                ).strip()
                if not description:
                    description = f"Imported skill: {normalized_name}"

                entry: dict[str, Any] = {
                    "name": resolved_name,
                    "purpose": str(skill_item.get("purpose", description)).strip() or description,
                    "description": description,
                    "roles": _normalize_string_list(skill_item.get("roles", [])),
                    "capabilities": _normalize_string_list(skill_item.get("capabilities", [])),
                    "keywords": _normalize_string_list(skill_item.get("keywords", [])),
                    "mcp_servers": _normalize_string_list(skill_item.get("mcp_servers", [])),
                    "source_provider": str(source.get("provider", "community")).strip().lower() or "community",
                    "source_repo": repo,
                    "source_branch": branch,
                    "source_batch": source_name,
                    "source_path": skill_path,
                    "source_url": raw_url,
                    "source_license": license_text,
                    "source_expected_sha256": expected_sha256,
                    "content_sha256": content_hash,
                    "last_sync": now_iso,
                    "content": content_clean,
                }
                existing[resolved_name] = entry
                pulled += 1
                if resolved_name not in changed:
                    changed.append(resolved_name)

            if max_items > 0 and pulled >= max_items:
                break

        registry_payload = {
            "updated_at": now_iso,
            "skills": sorted(existing.values(), key=lambda row: str(row.get("name", "")).strip().lower()),
        }
        _write_json(self.skills_registry_path, registry_payload)

        synced = self.sync_skill_library(force=force, targets=selected_targets)
        return {
            "updated_at": now_iso,
            "pulled": pulled,
            "changed": len(changed),
            "synced": len(synced),
            "skipped": skipped,
            "errors": errors,
            "warnings": warnings,
            "changed_skills": changed,
            "synced_skills": synced,
            "targets": sorted(selected_targets),
        }

    def skills_status(self) -> dict[str, Any]:
        library_count = len(self.skill_library_entries())
        registry_count = len(self.skill_registry_entries())
        cloud_count = _count_skill_dirs(self.skills_root)
        agents_count = _count_skill_dirs(self.skills_root_agents)
        claude_count = _count_skill_dirs(self.skills_root_claude)
        return {
            "library_skills": library_count,
            "registry_skills": registry_count,
            "cloud_skills": cloud_count,
            "agents_skills": agents_count,
            "claude_skills": claude_count,
        }

    def guidance_for_task(
        self,
        *,
        role: str,
        description: str,
        required_capabilities: set[str],
        limit: int = 4,
    ) -> dict[str, Any]:
        role_key = role.strip().lower()
        required = {item.strip().lower() for item in required_capabilities if item.strip()}
        text_blob = description.lower()

        # Build success rate map from usage stats
        usage_stats = self.skill_usage_stats(limit=50)
        success_map: dict[str, float] = {}
        uses_map: dict[str, int] = {}
        for stat in usage_stats:
            sname = str(stat.get("skill", "")).strip().lower()
            if sname:
                success_map[sname] = float(stat.get("success_rate", 50.0))
                uses_map[sname] = int(stat.get("total_uses", 0))

        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in self.skill_library_entries():
            capabilities = _to_lower_set(entry.get("capabilities", []))
            roles = _to_lower_set(entry.get("roles", []))
            keywords = _to_lower_set(entry.get("keywords", []))

            score = 0.0
            score += len(required.intersection(capabilities)) * 3
            if role_key and role_key in roles:
                score += 2
            if any(keyword and keyword in text_blob for keyword in keywords):
                score += 1

            # Boost/penalize by success rate
            skill_name = str(entry.get("name", "")).strip().lower()
            if skill_name in success_map and uses_map.get(skill_name, 0) >= 2:
                rate = success_map[skill_name]
                if rate >= 80:
                    score += 2  # proven skill
                elif rate < 40:
                    score -= 1  # unreliable skill

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda row: row[0], reverse=True)
        selected = [row[1] for row in scored[: max(1, limit)]]

        recommended_mcp: set[str] = set()
        for entry in selected:
            for item in _to_lower_set(entry.get("mcp_servers", [])):
                recommended_mcp.add(item)

        for item in self._catalog_items():
            if str(item.get("category", "")).strip().lower() != "mcp":
                continue
            capabilities = _to_lower_set(item.get("capabilities", []))
            if required.intersection(capabilities):
                name = str(item.get("name", "")).strip().lower()
                if name:
                    recommended_mcp.add(name)

        active_mcp = set()
        payload = _load_json(self.mcp_path, default={"servers": []})
        servers = payload.get("servers", [])
        if isinstance(servers, list):
            for server in servers:
                if not isinstance(server, dict):
                    continue
                if not _to_bool(server.get("enabled", False)):
                    continue
                name = str(server.get("name", "")).strip().lower()
                if name:
                    active_mcp.add(name)

        lines: list[str] = []
        if selected:
            lines.append("Skills aplicables:")
            for entry in selected:
                name = str(entry.get("name", "")).strip().lower()
                purpose = str(entry.get("purpose", entry.get("description", ""))).strip()
                # Annotate with success rate ranking
                rate = success_map.get(name)
                uses = uses_map.get(name, 0)
                if rate is not None and uses >= 2:
                    if rate >= 80:
                        tag = " [RECOMENDADO - {:.0f}% exito]".format(rate)
                    elif rate < 40:
                        tag = " [USAR CON PRECAUCION - {:.0f}% exito]".format(rate)
                    else:
                        tag = " [{:.0f}% exito]".format(rate)
                else:
                    tag = ""
                lines.append(f"- {name}: {purpose}{tag}")

        if recommended_mcp:
            lines.append("MCP recomendados:")
            for item in sorted(recommended_mcp):
                status = "activo" if item in active_mcp else "registrar"
                lines.append(f"- {item} ({status})")

        return {
            "skills": [str(item.get("name", "")).strip().lower() for item in selected],
            "recommended_mcp": sorted(recommended_mcp),
            "active_mcp": sorted(active_mcp),
            "text": "\n".join(lines),
        }

    def mcp_doctor(
        self,
        *,
        timeout: int = 20,
        enable_healthy: bool = False,
        enable_sensitive: bool = False,
    ) -> dict[str, Any]:
        payload = _load_json(self.mcp_path, default={"servers": []})
        servers = payload.get("servers", [])
        if not isinstance(servers, list):
            servers = []

        reports: list[dict[str, Any]] = []
        checked_at = datetime.now(timezone.utc).isoformat()
        healthy_count = 0
        enabled_count = 0
        auto_enabled = 0
        skipped_sensitive = 0

        for item in servers:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", "")).strip()
            args = item.get("args", [])
            args_list = [str(arg) for arg in args] if isinstance(args, list) else []
            health_ok, reason = self._probe_mcp_command(command=command, args=args_list, timeout=timeout)
            status = "healthy" if health_ok else "unhealthy"

            if health_ok:
                healthy_count += 1
                if enable_healthy:
                    requires_approval = _to_bool(item.get("requires_approval", False))
                    if requires_approval and not enable_sensitive:
                        skipped_sensitive += 1
                    else:
                        if not _to_bool(item.get("enabled", False)):
                            auto_enabled += 1
                        item["enabled"] = True

            if _to_bool(item.get("enabled", False)):
                enabled_count += 1

            item["health_status"] = status
            item["health_reason"] = reason
            item["last_checked"] = checked_at

            reports.append(
                {
                    "name": str(item.get("name", "")).strip().lower(),
                    "status": status,
                    "reason": reason,
                    "enabled": _to_bool(item.get("enabled", False)),
                }
            )

        payload["servers"] = servers
        _write_json(self.mcp_path, payload)

        return {
            "checked_at": checked_at,
            "total": len(reports),
            "healthy": healthy_count,
            "enabled": enabled_count,
            "auto_enabled": auto_enabled,
            "skipped_sensitive": skipped_sensitive,
            "reports": reports,
        }

    def skill_coverage(self, *, runtime_dir: Path | None = None) -> dict[str, Any]:
        directory = (runtime_dir or self.runtime_dir).resolve()
        events_path = directory / "events.jsonl"
        if not events_path.exists():
            return {
                "total_task_execution": 0,
                "skill_guidance_events": 0,
                "coverage_percent": 0.0,
            }

        total_task_execution = 0
        guidance_events = 0
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            event_type = str(record.get("event_type", "")).strip().lower()
            if event_type == "task_execution":
                total_task_execution += 1
            elif event_type == "skill_mcp_guidance":
                guidance_events += 1

        percent = _safe_percent(guidance_events, total_task_execution)
        return {
            "total_task_execution": total_task_execution,
            "skill_guidance_events": guidance_events,
            "coverage_percent": percent,
        }

    def record_skill_usage(
        self,
        *,
        skill_name: str,
        task_id: str,
        agent_id: str,
        role: str,
        success: bool,
        duration_ms: int = 0,
    ) -> None:
        """Registra el uso de una skill por un agente."""
        tracker_path = self.runtime_dir / "skill_usage.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "skill": skill_name.strip().lower(),
            "task_id": task_id,
            "agent_id": agent_id,
            "role": role,
            "success": success,
            "duration_ms": duration_ms,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with tracker_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError:
            pass

    def skill_usage_stats(self, limit: int = 20) -> list[dict[str, Any]]:
        """Estadisticas de uso de skills: veces usado, tasa de exito, ultimo uso."""
        tracker_path = self.runtime_dir / "skill_usage.jsonl"
        if not tracker_path.exists():
            return []

        stats: dict[str, dict[str, Any]] = {}
        try:
            raw = tracker_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    skill = str(record.get("skill", "")).strip()
                    if not skill:
                        continue
                    if skill not in stats:
                        stats[skill] = {
                            "skill": skill,
                            "total_uses": 0,
                            "successes": 0,
                            "failures": 0,
                            "agents": set(),
                            "roles": set(),
                            "last_used": "",
                            "avg_duration_ms": 0,
                            "total_duration_ms": 0,
                        }
                    s = stats[skill]
                    s["total_uses"] += 1
                    if record.get("success"):
                        s["successes"] += 1
                    else:
                        s["failures"] += 1
                    s["agents"].add(record.get("agent_id", ""))
                    s["roles"].add(record.get("role", ""))
                    s["last_used"] = record.get("ts", "")
                    s["total_duration_ms"] += int(record.get("duration_ms", 0))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

        # Compute averages and convert sets
        result = []
        for s in stats.values():
            total = s["total_uses"]
            s["success_rate"] = round(s["successes"] / total * 100, 1) if total > 0 else 0.0
            s["avg_duration_ms"] = s["total_duration_ms"] // total if total > 0 else 0
            s["agents"] = sorted(s["agents"] - {""})
            s["roles"] = sorted(s["roles"] - {""})
            del s["total_duration_ms"]
            result.append(s)

        # Sort by usage count descending
        result.sort(key=lambda x: x["total_uses"], reverse=True)
        return result[:limit]

    def skill_ranking_for_role(self, role: str, limit: int = 10) -> list[dict[str, Any]]:
        """Ranking de skills por tasa de exito para un rol especifico."""
        all_stats = self.skill_usage_stats(limit=100)
        role_stats = [s for s in all_stats if role in s.get("roles", [])]
        # Sort by success rate, then by usage count
        role_stats.sort(key=lambda x: (x.get("success_rate", 0), x.get("total_uses", 0)), reverse=True)
        return role_stats[:limit]

    def integrate_from_metadata(
        self,
        *,
        task_id: str,
        metadata: dict[str, Any],
        internet_allowed: bool,
    ) -> ToolIntegrationReport:
        requirements = metadata.get("tool_requirements", [])
        if not isinstance(requirements, list) or not requirements:
            return ToolIntegrationReport(success=True)

        report = ToolIntegrationReport(success=True)
        normalized_requirements = [self._normalize_requirement(item) for item in requirements]

        for requirement in normalized_requirements:
            name = str(requirement.get("name", "")).strip()
            if not name:
                report.success = False
                report.errors.append("missing_tool_name")
                continue

            required = _to_bool(requirement.get("required", True))
            uses_internet = _to_bool(requirement.get("uses_internet", True))
            if uses_internet and not internet_allowed:
                message = f"internet_tool_blocked:{name}"
                if required:
                    report.success = False
                    report.errors.append(message)
                else:
                    report.messages.append(f"optional_{message}")
                continue

            category = str(requirement.get("category", "cli")).strip().lower()
            if category == "skill":
                ok, detail = self._integrate_skill(requirement)
                if ok:
                    report.integrated_skills.append(name)
                    report.messages.append(detail)
                else:
                    if required:
                        report.success = False
                        report.errors.append(detail)
                    else:
                        report.messages.append(f"optional_failed:{detail}")
                continue

            if category in {"cli", "mcp"}:
                acquire_ok, acquire_message = self._acquire_tool(requirement)
                if not acquire_ok:
                    if required:
                        report.success = False
                        report.errors.append(acquire_message)
                        continue
                    report.messages.append(f"optional_failed:{acquire_message}")
                    requirement = dict(requirement)
                    requirement["enabled"] = False
                    report.messages.append(f"auto_disabled_due_to_acquire_failure:{name}")

                adapter_ok, adapter_message = self._integrate_adapter(requirement)
                if adapter_ok:
                    report.integrated_adapters.append(name)
                    report.messages.append(adapter_message)
                else:
                    if required:
                        report.success = False
                        report.errors.append(adapter_message)
                    else:
                        report.messages.append(f"optional_failed:{adapter_message}")
                    continue

                if category == "mcp":
                    mcp_ok, mcp_message = self._integrate_mcp_server(requirement)
                    if mcp_ok:
                        report.integrated_mcp_servers.append(name)
                        report.messages.append(mcp_message)
                    else:
                        if required:
                            report.success = False
                            report.errors.append(mcp_message)
                        else:
                            report.messages.append(f"optional_failed:{mcp_message}")
                continue

            unknown = f"unsupported_tool_category:{name}:{category}"
            if required:
                report.success = False
                report.errors.append(unknown)
            else:
                report.messages.append(f"optional_failed:{unknown}")

        self._append_registry(task_id=task_id, report=report)
        return report

    def suggest_requirements(
        self,
        required_capabilities: set[str],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        if not required_capabilities:
            return []

        items = self._catalog_items()
        scored: list[tuple[int, dict[str, Any]]] = []
        required = {item.strip().lower() for item in required_capabilities if item.strip()}
        for item in items:
            capabilities = _to_lower_set(item.get("capabilities", []))
            overlap = len(required.intersection(capabilities))
            if overlap <= 0:
                continue
            scored.append((overlap, dict(item)))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored[: max(1, limit)]]

    def _normalize_requirement(self, requirement: Any) -> dict[str, Any]:
        if isinstance(requirement, str):
            base = {"name": requirement}
        elif isinstance(requirement, dict):
            base = dict(requirement)
        else:
            return {}

        name = str(base.get("name", "")).strip().lower()
        if not name:
            return base

        catalog_item = self._catalog_by_name().get(name)
        if catalog_item is None:
            return base

        merged = dict(catalog_item)
        merged.update(base)
        return merged

    def _catalog_items(self) -> list[dict[str, Any]]:
        if not self.catalog_path.exists():
            return []
        raw = self.catalog_path.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = payload.get("tools", [])
        if not isinstance(items, list):
            return []
        output = []
        for item in items:
            if isinstance(item, dict):
                output.append(item)
        return output

    def _catalog_by_name(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in self._catalog_items():
            name = str(item.get("name", "")).strip().lower()
            if not name:
                continue
            result[name] = item
        return result

    def _integrate_adapter(self, requirement: dict[str, Any]) -> tuple[bool, str]:
        payload = _load_json(self.adapters_path, default={"external_adapters": []})
        items = payload.get("external_adapters", [])
        if not isinstance(items, list):
            items = []

        name = str(requirement.get("adapter_name", requirement.get("name", "tool"))).strip().lower()
        if not name:
            return False, "adapter_missing_name"

        adapter = {
            "type": "external_program",
            "name": name,
            "provider": str(requirement.get("provider", "custom")).strip().lower() or "custom",
            "model": str(requirement.get("model", f"{name}-tool")).strip() or f"{name}-tool",
            "channel": str(requirement.get("channel", "subscription")).strip().lower() or "subscription",
            "command": self._command_for_requirement(requirement),
            "capabilities": sorted(_to_lower_set(requirement.get("capabilities", ["analysis"]))),
            "role_targets": sorted(_to_lower_set(requirement.get("role_targets", []))),
            "priority": str(requirement.get("priority", "secondary")).strip().lower() or "secondary",
            "enabled": _to_bool(requirement.get("enabled", True)),
            "requires_approval": _to_bool(requirement.get("requires_approval", False)),
            "timeout_seconds": int(requirement.get("timeout_seconds", 120)),
            "cost_tier": int(requirement.get("cost_tier", 1)),
        }

        upserted = False
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip().lower() == name:
                items[index] = adapter
                upserted = True
                break
        if not upserted:
            items.append(adapter)

        payload["external_adapters"] = items
        _write_json(self.adapters_path, payload)
        return True, f"adapter_integrated:{name}"

    def _integrate_mcp_server(self, requirement: dict[str, Any]) -> tuple[bool, str]:
        payload = _load_json(self.mcp_path, default={"servers": []})
        servers = payload.get("servers", [])
        if not isinstance(servers, list):
            servers = []

        name = str(requirement.get("name", "")).strip().lower()
        command = self._command_for_requirement(requirement)
        if not name or not command:
            return False, "mcp_missing_name_or_command"

        server = {
            "name": name,
            "transport": str(requirement.get("transport", "stdio")).strip().lower() or "stdio",
            "command": command[0],
            "args": command[1:],
            "enabled": _to_bool(requirement.get("enabled", True)),
            "requires_approval": _to_bool(requirement.get("requires_approval", True)),
            "source_type": str(requirement.get("source_type", "npm")).strip().lower(),
            "source": str(requirement.get("source", "")).strip(),
        }

        upserted = False
        for index, item in enumerate(servers):
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip().lower() == name:
                servers[index] = server
                upserted = True
                break
        if not upserted:
            servers.append(server)
        payload["servers"] = servers
        _write_json(self.mcp_path, payload)
        return True, f"mcp_registered:{name}"

    def _integrate_skill(self, requirement: dict[str, Any]) -> tuple[bool, str]:
        name = str(requirement.get("name", "")).strip().lower()
        if not name:
            return False, "skill_missing_name"

        content = str(requirement.get("content", "")).strip()
        source_type = str(requirement.get("source_type", "builtin")).strip().lower()
        source = str(requirement.get("source", "")).strip()

        if not content and source_type == "url" and source:
            try:
                with urllib.request.urlopen(source, timeout=20) as response:
                    content = response.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                return False, f"skill_download_failed:{name}:{exc}"

        if not content:
            content = self._default_skill_content(requirement)

        slug = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-") or "skill"
        merged = dict(requirement)
        merged["content"] = content
        self._write_skill_to_targets(
            slug=slug,
            canonical_content=self._canonical_skill_content(merged),
            legacy_content=self._legacy_skill_content(merged),
            force=True,
            targets=_normalize_skill_targets(None),
        )
        return True, f"skill_integrated:{name}"

    def _canonical_skill_content(self, requirement: dict[str, Any]) -> str:
        name = str(requirement.get("name", "")).strip().lower() or "custom-skill"
        description = str(
            requirement.get("description", requirement.get("purpose", ""))
        ).strip() or "Imported skill"
        raw_content = str(requirement.get("content", "")).strip()
        if raw_content:
            parsed = _extract_skill_frontmatter(raw_content)
            if str(parsed.get("name", "")).strip() and str(parsed.get("description", "")).strip() and raw_content.lstrip().startswith("---"):
                return raw_content.strip() + "\n"
            body = raw_content
        else:
            body = self._default_skill_content(requirement)
        header = (
            "---\n"
            f"name: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=True)}\n"
            "---"
        )
        return f"{header}\n\n{body.strip()}\n"

    def _legacy_skill_content(self, requirement: dict[str, Any]) -> str:
        content = str(requirement.get("content", "")).strip()
        if content:
            return content.strip() + "\n"
        return self._default_skill_content(requirement).strip() + "\n"

    def _write_skill_to_targets(
        self,
        *,
        slug: str,
        canonical_content: str,
        legacy_content: str,
        force: bool,
        targets: set[str],
    ) -> bool:
        changed = False

        if "cloud" in targets:
            canonical_path = self.skills_root / slug / "SKILL.md"
            legacy_path = self.skills_root / slug / "skill.md"
            changed = _write_text_if_needed(canonical_path, canonical_content, force=force) or changed
            changed = _write_text_if_needed(legacy_path, legacy_content, force=force) or changed

        if "agents" in targets:
            agents_path = self.skills_root_agents / slug / "SKILL.md"
            changed = _write_text_if_needed(agents_path, canonical_content, force=force) or changed

        if "claude" in targets:
            claude_path = self.skills_root_claude / slug / "SKILL.md"
            changed = _write_text_if_needed(claude_path, canonical_content, force=force) or changed

        return changed

    def _acquire_tool(self, requirement: dict[str, Any]) -> tuple[bool, str]:
        if not _to_bool(requirement.get("acquire", False)):
            return True, "acquire_skipped"

        source_type = str(requirement.get("source_type", "")).strip().lower()
        source = str(requirement.get("source", "")).strip()
        if not source:
            return False, "acquire_missing_source"

        if source_type == "pip":
            return self._run_command(["python", "-m", "pip", "install", source], timeout=300)
        if source_type == "npm":
            install_root = self.runtime_dir / "tools" / "npm"
            install_root.mkdir(parents=True, exist_ok=True)
            return self._run_command(
                [
                    "cmd",
                    "/c",
                    "npm",
                    "install",
                    "--prefix",
                    str(install_root),
                    source,
                ],
                timeout=300,
            )
            
            if ok:
                # Post-install lock verification/update
                locked = self.tool_lock.get_locked_version(name)
                if not locked:
                    # Capture actual version if possible, for now just lock the 'source' string as 'version'
                    # unless it's a specific version specifier already.
                    self.tool_lock.lock_tool(name, source)
            return ok, msg
        if source_type == "git":
            target = self.runtime_dir / "tools" / "git" / _safe_slug(requirement.get("name", source))
            if target.exists():
                return True, "acquire_skipped_existing_clone"
            target.parent.mkdir(parents=True, exist_ok=True)
            return self._run_command(
                ["cmd", "/c", "git", "clone", source, str(target)],
                timeout=300,
            )

        return False, f"acquire_unsupported_source_type:{source_type}"

    def _run_command(self, args: list[str], timeout: int, max_retries: int = 3) -> tuple[bool, str]:
        import time
        attempt = 1
        last_error = ""
        while attempt <= max_retries:
            try:
                proc = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if proc.returncode == 0:
                    return True, "acquire_ok"
                last_error = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = f"acquire_exec_error:{exc}"
                
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            attempt += 1

        return False, f"acquire_failed after {max_retries} attempts: {last_error[:180]}"

    def _probe_mcp_command(self, *, command: str, args: list[str], timeout: int) -> tuple[bool, str]:
        command_clean = command.strip()
        if not command_clean:
            return False, "missing_command"

        lower = command_clean.lower()
        if lower in {"cmd", "powershell"}:
            return True, "shell_command"
        if Path(command_clean).is_absolute() and Path(command_clean).exists():
            return True, "absolute_path_exists"

        resolved = self._resolve_executable(command_clean)
        if resolved is None:
            return False, "command_not_found"

        if lower in {"python", "python3", "py"}:
            return True, "python_available"

        probe_args = list(args)
        if lower == "npx":
            if not probe_args:
                return False, "npx_missing_package"
            if "--help" not in probe_args:
                probe_args = probe_args + ["--help"]

        try:
            proc = subprocess.run(
                [resolved, *probe_args],
                capture_output=True,
                text=True,
                timeout=max(3, timeout),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"probe_error:{exc}"

        if proc.returncode == 0:
            return True, "probe_ok"
        stderr = (proc.stderr or "").strip().replace("\n", " ")
        stdout = (proc.stdout or "").strip().replace("\n", " ")
        detail = stderr or stdout or f"exit_{proc.returncode}"
        return False, f"probe_failed:{detail[:140]}"

    @staticmethod
    def _resolve_executable(command: str) -> str | None:
        binary = command.strip()
        if not binary:
            return None
        candidates = [binary]
        if os.name == "nt" and not binary.lower().endswith((".cmd", ".exe", ".bat")):
            candidates.extend([f"{binary}.cmd", f"{binary}.exe", f"{binary}.bat"])
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def _command_for_requirement(self, requirement: dict[str, Any]) -> list[str]:
        raw_command = requirement.get("command")
        if isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
            return [str(item) for item in raw_command]

        source_type = str(requirement.get("source_type", "npm")).strip().lower()
        source = str(requirement.get("source", requirement.get("name", ""))).strip()
        if source_type == "npm":
            npx_bin = "npx.cmd" if os.name == "nt" else "npx"
            category = str(requirement.get("category", "cli")).strip().lower()
            if category == "mcp":
                return [npx_bin, "-y", source]
            return [npx_bin, "-y", source, "{prompt}"]
        if source_type == "pip":
            module = str(requirement.get("python_module", _guess_python_module(source))).strip()
            return ["python", "-m", module, "{prompt}"]
        if source_type == "git":
            local_cmd = str(requirement.get("local_command", "")).strip()
            if local_cmd:
                return ["cmd", "/c", local_cmd]
        return ["python", "-c", "print('tool not configured')"]

    def _default_skill_content(self, requirement: dict[str, Any]) -> str:
        name = str(requirement.get("name", "custom_skill")).strip().lower()
        description = str(requirement.get("description", "Skill auto-generada para AI Team")).strip()
        capabilities = sorted(_to_lower_set(requirement.get("capabilities", [])))
        bullets = "\n".join(f"- {item}" for item in capabilities) or "- analysis"
        return (
            f"# {name}\n\n"
            f"{description}\n\n"
            "## Objetivo\n"
            "Usar esta skill cuando la tarea requiera este dominio.\n\n"
            "## Capacidades\n"
            f"{bullets}\n\n"
            "## Guardrails\n"
            "- No exponer secretos ni credenciales.\n"
            "- Priorizar cambios pequenos, verificables y con evidencia.\n"
        )

    def _append_registry(self, *, task_id: str, report: ToolIntegrationReport) -> None:
        payload = _load_json(self.registry_path, default={"entries": []})
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        entries.append(
            {
                "task_id": task_id,
                "success": report.success,
                "integrated_adapters": report.integrated_adapters,
                "integrated_skills": report.integrated_skills,
                "integrated_mcp_servers": report.integrated_mcp_servers,
                "messages": report.messages,
                "errors": report.errors,
            }
        )
        payload["entries"] = entries[-200:]
        _write_json(self.registry_path, payload)


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return dict(default)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return dict(default)
    if not isinstance(payload, dict):
        return dict(default)
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _to_lower_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _guess_python_module(package: str) -> str:
    return package.strip().replace("-", "_")


def _safe_slug(value: Any) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    return text.strip("-") or "tool"


def _safe_percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def _normalize_skill_targets(targets: set[str] | None) -> set[str]:
    if not targets:
        return {"cloud", "agents", "claude"}
    normalized = {
        str(item).strip().lower()
        for item in targets
        if str(item).strip().lower() in {"cloud", "agents", "claude"}
    }
    if not normalized:
        return {"cloud", "agents", "claude"}
    return normalized


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text and text not in output:
            output.append(text)
    return output


def _repo_is_allowed(repo: str, policy: dict[str, Any]) -> bool:
    repo_clean = repo.strip().lower()
    if not repo_clean:
        return False

    allow_repos = _normalize_string_list(policy.get("allow_repos", []))
    if allow_repos and repo_clean not in allow_repos:
        return False

    allow_owners = _normalize_string_list(policy.get("allow_owners", []))
    if allow_owners:
        owner = repo_clean.split("/", 1)[0]
        if owner not in allow_owners:
            return False
    return True


def _extract_skill_frontmatter(content: str) -> dict[str, str]:
    text = str(content or "")
    if not text.lstrip().startswith("---"):
        return {}

    lines = text.splitlines()
    if not lines:
        return {}
    if lines[0].strip() != "---":
        return {}

    parsed: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_clean = key.strip().lower()
        value_clean = value.strip().strip('"').strip("'")
        if key_clean:
            parsed[key_clean] = value_clean
    return parsed


def _write_text_if_needed(path: Path, content: str, *, force: bool) -> bool:
    normalized = content if content.endswith("\n") else f"{content}\n"
    if path.exists() and not force:
        existing = path.read_text(encoding="utf-8")
        if existing == normalized:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    return True


def _count_skill_dirs(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    count = 0
    for item in root.iterdir():
        if item.is_dir():
            count += 1
    return count

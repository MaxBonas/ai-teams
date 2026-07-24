from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PORT = 8010
FRONTEND_PORT = 9490
CLI_COMMANDS = {
    "codex": ("codex.cmd", "codex"),
    "antigravity": ("agy.exe", "agy"),
    "opencode": ("opencode.cmd", "opencode"),
    "ollama": ("ollama.exe", "ollama"),
    "lmstudio": ("lms.exe", "lms"),
}


def _command_present(candidates: tuple[str, ...]) -> bool:
    return any(shutil.which(candidate) for candidate in candidates)


def _cli_inventory() -> dict[str, bool]:
    return {name: _command_present(commands) for name, commands in CLI_COMMANDS.items()}


def _redact(text: str, *, fixture_root: Path | None = None) -> str:
    redacted = str(text)
    replacements: list[tuple[Path, str]] = []
    if fixture_root is not None:
        replacements.append((fixture_root, "<fixture_root>"))
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        replacements.append((Path(user_profile), "<user_profile>"))
    replacements.append((ROOT, "<repo>"))
    replacements.sort(key=lambda item: len(str(item[0])), reverse=True)
    for path, marker in replacements:
        for value in {str(path), path.as_posix()}:
            redacted = re.sub(re.escape(value), marker, redacted, flags=re.IGNORECASE)
    if len(redacted) <= 2000:
        return redacted
    return redacted[:800] + "\n...[truncated]...\n" + redacted[-1200:]


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port_is_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    options: dict[str, Any] = {
        "cwd": ROOT,
        "env": env,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if capture_output:
        options["capture_output"] = True
    else:
        # start_ide desacopla backend/frontend. Usar pipes aquí mantiene sus
        # handles heredados abiertos y hace que subprocess espere al teardown.
        options["stdout"] = subprocess.DEVNULL
        options["stderr"] = subprocess.DEVNULL
    return subprocess.run(command, check=False, **options)


def _wait_for_closed_ports(timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_closed(BACKEND_PORT) and _port_is_closed(FRONTEND_PORT):
            return True
        time.sleep(0.5)
    return False


def _fixture_summary(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        required_tables = {"agents", "goals", "issues", "runs", "wakeup_requests"}
        observed_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = sorted(required_tables - observed_tables)
        table_count = len(observed_tables)
        if missing_tables:
            raise RuntimeError(
                "El proyecto fixture carece de tablas requeridas: "
                + ", ".join(missing_tables)
            )
        issue_count = int(conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0])
        goal_count = int(conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0])
    return {
        "issues": issue_count,
        "goals": goal_count,
        "tables": table_count,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_lower_hex(value: str | None, lengths: set[int]) -> bool:
    return bool(
        value
        and len(value) in lengths
        and all(character in "0123456789abcdef" for character in value)
    )


def _github_provenance(revision: str) -> tuple[bool, dict[str, str | None]]:
    provenance = {
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job": os.environ.get("GITHUB_JOB"),
        "source_sha": os.environ.get("AITEAM_EXPECTED_SOURCE_SHA"),
        "trigger_sha": os.environ.get("GITHUB_SHA"),
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
    }
    independent = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and provenance["runner_os"] == "Windows"
        and provenance["runner_arch"] == "X64"
        and provenance["source_sha"] == revision
        and all(
            provenance[key]
            for key in ("repository", "run_id", "run_attempt", "job")
        )
    )
    return bool(independent), provenance


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aceptación I.1.4 para un checkout Windows limpio. No instala CLIs, "
            "no prueba credenciales y no ejecuta modelos."
        )
    )
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument(
        "--source-kind",
        choices=("git_checkout", "release_archive"),
        default="git_checkout",
    )
    parser.add_argument(
        "--source-revision",
        help="Revisión del manifiesto; obligatoria para release_archive.",
    )
    parser.add_argument(
        "--archive-sha256",
        help="SHA-256 verificado del ZIP; obligatorio para release_archive.",
    )
    args = parser.parse_args()

    receipt_path = args.receipt.resolve()
    fixture_root = args.fixture_root.resolve()
    fixture_name = f"I1 Clean Room {uuid.uuid4().hex[:8]}"
    fixture_path = fixture_root / fixture_name
    env = dict(os.environ)
    env["AITEAM_NO_BROWSER"] = "1"
    env["AITEAM_PROJECTS_ROOT"] = str(fixture_root)
    env["AITEAM_USER_CONFIG_DIR"] = str(fixture_root / ".user-config")
    env["NO_COLOR"] = "1"

    receipt: dict[str, Any] = {
        "schema_version": "windows_clean_room_acceptance_v1",
        "environment_class": "unclassified",
        "independent_machine": False,
        "host": {
            "os": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "python_bootstrap": platform.python_version(),
        },
        "source": {
            "kind": args.source_kind,
            "checkout": (
                "actions_checkout"
                if os.environ.get("GITHUB_ACTIONS") == "true"
                else "existing_checkout"
            ),
            "revision": None,
            "archive_sha256": args.archive_sha256,
        },
        "ci_provenance": None,
        "steps": [],
        "global_cli_inventory_before": _cli_inventory(),
        "global_cli_inventory_after": None,
        "fixture": None,
        "ok": False,
        "promotion_allowed": False,
    }
    started = False

    def step(
        name: str,
        command: list[str],
        timeout: int,
        *,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        started_at = time.monotonic()
        proc = _run(command, env=env, timeout=timeout, capture_output=capture_output)
        receipt["steps"].append(
            {
                "name": name,
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "duration_seconds": round(time.monotonic() - started_at, 3),
            }
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            raise RuntimeError(f"{name}: {_redact(detail, fixture_root=fixture_root)}")
        return proc

    try:
        if os.name != "nt":
            raise RuntimeError("I.1.4 requiere Windows nativo")
        if platform.machine().lower() not in {"amd64", "x86_64"}:
            raise RuntimeError("I.1.4 requiere arquitectura Windows x86_64")
        if not _port_is_free(BACKEND_PORT) or not _port_is_free(FRONTEND_PORT):
            raise RuntimeError(
                "Los puertos 8010/9490 ya están ocupados; no se detendrán procesos ajenos"
            )

        if args.source_kind == "release_archive":
            if not args.source_revision or not args.archive_sha256:
                raise RuntimeError(
                    "release_archive exige source_revision y archive_sha256"
                )
            if not _is_lower_hex(args.archive_sha256, {64}):
                raise RuntimeError("archive_sha256 no es un SHA-256 válido")
            if not _is_lower_hex(args.source_revision, {40, 64}):
                raise RuntimeError("source_revision no es una revisión Git válida")
            revision = args.source_revision
            receipt["source"]["checkout"] = "extracted_release_archive"
            receipt["steps"].append(
                {
                    "name": "source_revision",
                    "ok": True,
                    "exit_code": 0,
                    "duration_seconds": 0.0,
                }
            )
        else:
            revision = step(
                "source_revision",
                ["git", "rev-parse", "HEAD"],
                30,
            ).stdout.strip()
        receipt["source"]["revision"] = revision
        independent, provenance = _github_provenance(revision)
        receipt["independent_machine"] = independent
        receipt["environment_class"] = (
            "independent_ephemeral_ci" if independent else "local_existing_host"
        )
        receipt["ci_provenance"] = provenance if independent else None

        step("bootstrap_first", ["cmd.exe", "/d", "/c", "scripts\\prepare_dev_env.bat"], 1200)
        step("bootstrap_second", ["cmd.exe", "/d", "/c", "scripts\\prepare_dev_env.bat"], 1200)

        audit_proc = step(
            "installation_audit",
            [
                "cmd.exe",
                "/d",
                "/c",
                "scripts\\python_local.bat",
                "scripts\\audit_installation_support.py",
                "--json",
                "--strict",
            ],
            120,
        )
        audit = json.loads(audit_proc.stdout)
        if not audit.get("control_plane_ready"):
            raise RuntimeError("installation_audit no dejó el control plane listo")
        receipt["installation_audit"] = {
            "schema_version": audit.get("schema_version"),
            "contract_updated_at": audit.get("contract_updated_at"),
            "support_id": audit.get("host", {}).get("support_id"),
            "support_status": audit.get("host", {}).get("support_status"),
            "control_plane_ready": True,
            "live_runs_status": audit.get("live_runs", {}).get("status"),
            "runtimes": [
                {
                    "id": item.get("id"),
                    "version": item.get("version"),
                    "minimum_version": item.get("minimum_version"),
                    "ready": item.get("ready"),
                }
                for item in audit.get("runtimes", [])
            ],
        }
        if not receipt["installation_audit"]["runtimes"] or not all(
            item["ready"] for item in receipt["installation_audit"]["runtimes"]
        ):
            raise RuntimeError("installation_audit no conservó runtimes listos")

        step(
            "minimum_tests",
            [
                "cmd.exe",
                "/d",
                "/c",
                "scripts\\pytest_local.bat",
                "tests\\test_e2e_non_code_canary.py",
                (
                    "tests\\test_dev_lifecycle_contract.py::"
                    "test_bootstrap_requires_versioned_locks_and_has_concurrency_guards"
                ),
                "-q",
                "--tb=short",
            ],
            180,
        )

        step(
            "start",
            ["cmd.exe", "/d", "/c", "start_ide.bat"],
            180,
            capture_output=False,
        )
        started = True
        step(
            "backend_health",
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "
                    "-Uri http://127.0.0.1:8010/openapi.json; "
                    "if($r.StatusCode -ne 200){exit 1}"
                ),
            ],
            30,
        )
        step(
            "frontend_health",
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "
                    "-Uri http://127.0.0.1:9490; "
                    "if($r.StatusCode -ne 200){exit 1}"
                ),
            ],
            30,
        )

        fixture_root.mkdir(parents=True, exist_ok=True)
        step(
            "fixture_project_create",
            [
                "cmd.exe",
                "/d",
                "/c",
                "scripts\\python_local.bat",
                "-m",
                "aiteam.cli",
                "project",
                "create",
                fixture_name,
                "--task",
                "Validar instalación portable sin ejecutar un modelo",
            ],
            120,
        )
        db_path = fixture_path / ".aiteam" / "aiteam.db"
        if not db_path.is_file():
            raise RuntimeError("El proyecto fixture no creó .aiteam/aiteam.db")
        summary = _fixture_summary(db_path)
        if summary["issues"] != 1 or summary["tables"] < 5:
            raise RuntimeError(f"Fixture incompleto: {summary}")
        receipt["fixture"] = {"created": True, **summary}

        step("stop", ["cmd.exe", "/d", "/c", "stop_ide.bat"], 60)
        started = False
        if not _wait_for_closed_ports():
            raise RuntimeError("start/stop no liberó los puertos 8010/9490")
        receipt["steps"].append(
            {"name": "ports_released", "ok": True, "exit_code": 0, "duration_seconds": 0.0}
        )

        original_hash = _sha256_file(db_path)
        step(
            "migration_dry_run",
            [
                "cmd.exe",
                "/d",
                "/c",
                "scripts\\python_local.bat",
                "scripts\\migrate_to_v2.py",
                "--db",
                str(db_path),
                "--json",
            ],
            120,
        )
        migration = step(
            "migration_apply_with_backup",
            [
                "cmd.exe",
                "/d",
                "/c",
                "scripts\\python_local.bat",
                "scripts\\migrate_to_v2.py",
                "--db",
                str(db_path),
                "--apply",
                "--json",
            ],
            120,
        )
        migration_payload = json.loads(migration.stdout)
        backup_value = migration_payload.get("backup_path")
        if not backup_value:
            raise RuntimeError("La migración aplicada no produjo backup")
        backup_path = Path(str(backup_value)).resolve()
        if backup_path.parent != db_path.parent or not backup_path.is_file():
            raise RuntimeError("La migración devolvió un backup fuera del proyecto fixture")
        backup_hash = _sha256_file(backup_path)
        if backup_hash != original_hash:
            raise RuntimeError("El backup no coincide con la base previa a migración")
        rollback_probe = sqlite3.connect(db_path)
        try:
            rollback_probe.execute(
                "CREATE TABLE acceptance_rollback_probe (id INTEGER)"
            )
            rollback_probe.commit()
        finally:
            rollback_probe.close()
        shutil.copy2(backup_path, db_path)
        restored_hash = _sha256_file(db_path)
        if restored_hash != original_hash:
            raise RuntimeError("La restauración no recuperó los bytes originales")
        receipt["steps"].append(
            {
                "name": "database_rollback_restore",
                "ok": True,
                "exit_code": 0,
                "duration_seconds": 0.0,
            }
        )
        receipt["database_rollback"] = {
            "backup_created": True,
            "backup_matches_original": True,
            "restored_matches_original": True,
        }

        receipt["global_cli_inventory_after"] = _cli_inventory()
        introduced = [
            name
            for name, was_present in receipt["global_cli_inventory_before"].items()
            if not was_present and receipt["global_cli_inventory_after"][name]
        ]
        if introduced:
            raise RuntimeError(
                "El bootstrap instaló CLIs globales sin permiso: " + ", ".join(introduced)
            )

        receipt["ok"] = True
        receipt["promotion_allowed"] = receipt["independent_machine"]
        return 0
    # El recibo y el teardown deben producirse incluso ante un fallo inesperado
    # del bootstrap o de un subprocess externo.
    except Exception as exc:  # noqa: BLE001
        receipt["failure"] = _redact(str(exc), fixture_root=fixture_root)
        return 1
    finally:
        if started:
            _run(["cmd.exe", "/d", "/c", "stop_ide.bat"], env=env, timeout=60)
        receipt["global_cli_inventory_after"] = (
            receipt["global_cli_inventory_after"] or _cli_inventory()
        )
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())

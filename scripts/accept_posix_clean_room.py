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
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PORT = 8010
FRONTEND_PORT = 9490
CLI_COMMANDS = {
    "codex": ("codex",),
    "antigravity": ("agy",),
    "opencode": ("opencode",),
    "ollama": ("ollama",),
    "lmstudio": ("lms",),
}
SUPPORTED_SYSTEMS = {"linux": "Linux", "darwin": "macOS"}
SUPPORTED_ARCHITECTURES = {"x86_64", "amd64", "arm64", "aarch64"}


def _command_present(candidates: tuple[str, ...]) -> bool:
    return any(shutil.which(candidate) for candidate in candidates)


def _cli_inventory() -> dict[str, bool]:
    return {name: _command_present(commands) for name, commands in CLI_COMMANDS.items()}


def _redact(text: str, *, fixture_root: Path | None = None) -> str:
    redacted = str(text)
    replacements: list[tuple[Path, str]] = []
    if fixture_root is not None:
        replacements.append((fixture_root, "<fixture_root>"))
    home = os.environ.get("HOME")
    if home:
        replacements.append((Path(home), "<home>"))
    replacements.append((ROOT, "<repo>"))
    replacements.sort(key=lambda item: len(str(item[0])), reverse=True)
    for path, marker in replacements:
        for value in {str(path), path.as_posix()}:
            redacted = re.sub(re.escape(value), marker, redacted)
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


def _wait_for_closed_ports(timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_closed(BACKEND_PORT) and _port_is_closed(FRONTEND_PORT):
            return True
        time.sleep(0.5)
    return False


def _wait_for_url(url: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "sin respuesta"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"{url} no quedó healthy: {last_error}")


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        capture_output=True,
        check=False,
    )


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
        if missing_tables:
            raise RuntimeError(
                "El proyecto fixture carece de tablas requeridas: "
                + ", ".join(missing_tables)
            )
        return {
            "issues": int(conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]),
            "goals": int(conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]),
            "tables": len(observed_tables),
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


def _github_provenance(
    revision: str, *, system: str
) -> tuple[bool, dict[str, str | None]]:
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
    expected_runner_os = SUPPORTED_SYSTEMS[system]
    independent = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and provenance["runner_os"] == expected_runner_os
        and provenance["runner_arch"] in {"X64", "ARM64"}
        and provenance["source_sha"] == revision
        and all(
            provenance[key] for key in ("repository", "run_id", "run_attempt", "job")
        )
    )
    return bool(independent), provenance


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aceptación limpia para Linux/macOS. No instala CLIs, no prueba "
            "credenciales y no ejecuta modelos."
        )
    )
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument(
        "--source-kind",
        choices=("git_checkout", "release_archive"),
        default="git_checkout",
    )
    parser.add_argument("--source-revision")
    parser.add_argument("--archive-sha256")
    args = parser.parse_args()

    receipt_path = args.receipt.resolve()
    fixture_root = args.fixture_root.resolve()
    fixture_name = f"I8 Clean Room {uuid.uuid4().hex[:8]}"
    fixture_path = fixture_root / fixture_name
    env = dict(os.environ)
    env["AITEAM_NO_BROWSER"] = "1"
    env["AITEAM_PROJECTS_ROOT"] = str(fixture_root)
    env["AITEAM_USER_CONFIG_DIR"] = str(fixture_root / ".user-config")
    env["NO_COLOR"] = "1"
    system = platform.system().lower()
    architecture = platform.machine().lower()

    receipt: dict[str, Any] = {
        "schema_version": "posix_clean_room_acceptance_v1",
        "environment_class": "unclassified",
        "independent_machine": False,
        "host": {
            "os": system,
            "architecture": architecture,
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
    start_process: subprocess.Popen[bytes] | None = None

    def step(
        name: str, command: list[str], timeout: int
    ) -> subprocess.CompletedProcess[str]:
        started_at = time.monotonic()
        proc = _run(command, env=env, timeout=timeout)
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

    def health_step(name: str, url: str) -> None:
        started_at = time.monotonic()
        try:
            _wait_for_url(url)
        except RuntimeError:
            receipt["steps"].append(
                {
                    "name": name,
                    "ok": False,
                    "exit_code": 1,
                    "duration_seconds": round(time.monotonic() - started_at, 3),
                }
            )
            raise
        receipt["steps"].append(
            {
                "name": name,
                "ok": True,
                "exit_code": 0,
                "duration_seconds": round(time.monotonic() - started_at, 3),
            }
        )

    try:
        if system not in SUPPORTED_SYSTEMS:
            raise RuntimeError("Este harness requiere Linux o macOS nativo")
        if architecture not in SUPPORTED_ARCHITECTURES:
            raise RuntimeError(f"Arquitectura POSIX no soportada: {architecture}")
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
                "source_revision", ["git", "rev-parse", "HEAD"], 30
            ).stdout.strip()
        receipt["source"]["revision"] = revision
        independent, provenance = _github_provenance(revision, system=system)
        receipt["independent_machine"] = independent
        receipt["environment_class"] = (
            "independent_ephemeral_ci" if independent else "local_existing_host"
        )
        receipt["ci_provenance"] = provenance if independent else None

        step("bootstrap_first", ["sh", "scripts/prepare_dev_env.sh"], 1200)
        step("bootstrap_second", ["sh", "scripts/prepare_dev_env.sh"], 1200)
        audit_proc = step(
            "installation_audit",
            [
                "sh",
                "scripts/python_local.sh",
                "scripts/audit_installation_support.py",
                "--json",
                "--strict",
            ],
            120,
        )
        audit = json.loads(audit_proc.stdout)
        if not audit.get("control_plane_ready"):
            raise RuntimeError("installation_audit no dejó el control plane listo")
        runtimes = [
            {
                "id": item.get("id"),
                "version": item.get("version"),
                "minimum_version": item.get("minimum_version"),
                "ready": item.get("ready"),
            }
            for item in audit.get("runtimes", [])
        ]
        if not runtimes or not all(item["ready"] for item in runtimes):
            raise RuntimeError("installation_audit no conservó runtimes listos")
        receipt["installation_audit"] = {
            "schema_version": audit.get("schema_version"),
            "contract_updated_at": audit.get("contract_updated_at"),
            "support_id": audit.get("host", {}).get("support_id"),
            "support_status": audit.get("host", {}).get("support_status"),
            "control_plane_ready": True,
            "live_runs_status": audit.get("live_runs", {}).get("status"),
            "runtimes": runtimes,
        }

        step(
            "minimum_tests",
            [
                "sh",
                "scripts/pytest_local.sh",
                "tests/test_e2e_non_code_canary.py",
                (
                    "tests/test_dev_lifecycle_contract.py::"
                    "test_bootstrap_requires_versioned_locks_and_has_concurrency_guards"
                ),
                "-q",
                "--tb=short",
            ],
            180,
        )

        started_at = time.monotonic()
        start_process = subprocess.Popen(
            ["sh", "start_ide.sh"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        receipt["steps"].append(
            {
                "name": "start",
                "ok": True,
                "exit_code": 0,
                "duration_seconds": round(time.monotonic() - started_at, 3),
            }
        )
        health_step("backend_health", "http://127.0.0.1:8010/openapi.json")
        health_step("frontend_health", "http://127.0.0.1:9490")

        fixture_root.mkdir(parents=True, exist_ok=True)
        step(
            "fixture_project_create",
            [
                "sh",
                "scripts/python_local.sh",
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

        step("stop", ["sh", "stop_ide.sh"], 60)
        if start_process is not None:
            try:
                start_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                start_process.terminate()
                start_process.wait(timeout=10)
            start_process = None
        if not _wait_for_closed_ports():
            raise RuntimeError("start/stop no liberó los puertos 8010/9490")
        receipt["steps"].append(
            {
                "name": "ports_released",
                "ok": True,
                "exit_code": 0,
                "duration_seconds": 0.0,
            }
        )

        original_hash = _sha256_file(db_path)
        step(
            "migration_dry_run",
            [
                "sh",
                "scripts/python_local.sh",
                "scripts/migrate_to_v2.py",
                "--db",
                str(db_path),
                "--json",
            ],
            120,
        )
        migration = step(
            "migration_apply_with_backup",
            [
                "sh",
                "scripts/python_local.sh",
                "scripts/migrate_to_v2.py",
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
            raise RuntimeError(
                "La migración devolvió un backup fuera del proyecto fixture"
            )
        backup_hash = _sha256_file(backup_path)
        if backup_hash != original_hash:
            raise RuntimeError("El backup no coincide con la base previa a migración")
        with sqlite3.connect(db_path) as rollback_probe:
            rollback_probe.execute(
                "CREATE TABLE acceptance_rollback_probe (id INTEGER)"
            )
            rollback_probe.commit()
        shutil.copy2(backup_path, db_path)
        if _sha256_file(db_path) != original_hash:
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
                "El bootstrap instaló CLIs globales sin permiso: "
                + ", ".join(introduced)
            )

        receipt["ok"] = True
        receipt["promotion_allowed"] = receipt["independent_machine"]
        return 0
    except Exception as exc:  # noqa: BLE001
        receipt["failure"] = _redact(str(exc), fixture_root=fixture_root)
        return 1
    finally:
        if start_process is not None:
            _run(["sh", "stop_ide.sh"], env=env, timeout=60)
            try:
                start_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                start_process.terminate()
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

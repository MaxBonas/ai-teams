from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from aiteam.release_artifact import ReleaseArtifactError, verify_release_artifact

ROOT = Path(__file__).resolve().parents[1]


def _acceptance_script_name(platform_name: str = os.name) -> str:
    if platform_name == "nt":
        return "accept_windows_clean_room.py"
    if platform_name == "posix":
        return "accept_posix_clean_room.py"
    raise RuntimeError(f"Plataforma de aceptación no soportada: {platform_name}")


def _safe_child(parent: Path, name: str) -> Path:
    parent = parent.resolve()
    child = (parent / name).resolve()
    if child.parent != parent:
        raise RuntimeError("La ruta temporal no es hija directa del workspace")
    return child


def _remove_tree(path: Path, *, parent: Path) -> bool:
    resolved = path.resolve()
    if resolved.parent != parent.resolve():
        raise RuntimeError("Se rechazó limpiar una ruta fuera del workspace")
    if not resolved.exists():
        return True
    last_error: OSError | None = None
    for _ in range(5):
        try:
            shutil.rmtree(resolved)
            return True
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"No se pudo retirar la instalación temporal: {last_error}")


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_required_steps(receipt: dict[str, Any]) -> dict[str, Any]:
    contract_path = ROOT / "config" / "installation_support.v1.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    required = contract["release_acceptance_contract"]["required_steps"]
    steps = receipt.get("steps")
    if not isinstance(steps, list):
        steps = []
    observed: dict[str, bool] = {}
    duplicates: list[str] = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("name"), str):
            continue
        name = step["name"]
        if name in observed:
            duplicates.append(name)
        observed[name] = step.get("ok") is True
    missing = [name for name in required if name not in observed]
    failed = [name for name in required if observed.get(name) is False]
    return {
        "schema_version": "release_acceptance_contract_result_v1",
        "required_steps": required,
        "missing_steps": missing,
        "failed_steps": failed,
        "duplicate_steps": sorted(set(duplicates)),
        "ok": not missing and not failed and not duplicates,
    }


def _redact(text: str, *paths: Path) -> str:
    result = str(text)
    candidates = [path.resolve() for path in paths]
    for variable in ("USERPROFILE", "HOME"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value).resolve())
    for path in sorted(set(candidates), key=lambda item: len(str(item)), reverse=True):
        for value in (str(path), path.as_posix()):
            result = re.sub(
                re.escape(value),
                "<local_path>",
                result,
                flags=re.IGNORECASE,
            )
    if len(result) <= 2000:
        return result
    return result[:800] + "\n...[truncated]...\n" + result[-1200:]


def _redact_value(value: Any, *paths: Path) -> Any:
    if isinstance(value, str):
        return _redact(value, *paths)
    if isinstance(value, list):
        return [_redact_value(item, *paths) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, *paths) for key, item in value.items()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Acepta un ZIP de AI Teams desde fuera del paquete y retira toda la "
            "instalación temporal al terminar."
        )
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--allow-preview",
        action="store_true",
        help="Permite validar localmente un paquete no promocionable.",
    )
    args = parser.parse_args()

    workspace_root = args.workspace_root.resolve()
    receipt_path = args.receipt.resolve()
    token = uuid.uuid4().hex[:10]
    extraction_root = _safe_child(workspace_root, f"install-{token}")
    fixture_root = _safe_child(workspace_root, f"fixtures-{token}")
    receipt: dict[str, Any] = {
        "schema_version": "release_archive_acceptance_v1",
        "ok": False,
        "archive_verified": False,
        "package_acceptance": None,
        "cleanup": {
            "installation_removed": False,
            "fixtures_removed": False,
        },
        "promotion_allowed": False,
    }
    redaction_paths = (
        workspace_root,
        args.archive.resolve().parent,
        receipt_path.parent,
    )
    inner_receipt: Path | None = None
    exit_code = 1
    try:
        if receipt_path == extraction_root or receipt_path == fixture_root:
            raise RuntimeError("El recibo debe vivir fuera de las rutas temporales")
        workspace_root.mkdir(parents=True, exist_ok=True)
        verification = verify_release_artifact(
            args.archive,
            checksum_path=args.checksum,
            require_promotable=not args.allow_preview,
        )
        receipt["archive_verified"] = True
        receipt["archive"] = {
            "version": verification.version,
            "revision": verification.revision,
            "sha256": verification.archive_sha256,
            "files_verified": verification.files_verified,
            "promotion_allowed": verification.promotion_allowed,
        }

        extraction_root.mkdir()
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(extraction_root)
        package_root = extraction_root / verification.root_directory
        acceptance_name = _acceptance_script_name()
        acceptance_script = package_root / "scripts" / acceptance_name
        if not acceptance_script.is_file():
            raise RuntimeError("El paquete no contiene el harness de aceptación")
        inner_receipt = workspace_root / f"package-receipt-{token}.json"
        env = dict(os.environ)
        env["AITEAM_EXPECTED_SOURCE_SHA"] = verification.revision
        process = subprocess.run(
            [
                sys.executable,
                str(acceptance_script),
                "--receipt",
                str(inner_receipt),
                "--fixture-root",
                str(fixture_root),
                "--source-kind",
                "release_archive",
                "--source-revision",
                verification.revision,
                "--archive-sha256",
                verification.archive_sha256,
            ],
            cwd=package_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )
        if not inner_receipt.is_file():
            raise RuntimeError(
                f"El harness del paquete terminó con {process.returncode} sin recibo"
            )
        package_receipt = json.loads(inner_receipt.read_text(encoding="utf-8"))
        inner_receipt.unlink()
        inner_receipt = None
        receipt["package_acceptance"] = package_receipt
        if process.returncode != 0 or package_receipt.get("ok") is not True:
            failure = package_receipt.get("failure", f"exit {process.returncode}")
            raise RuntimeError(f"Falló la aceptación interna: {failure}")
        exit_code = 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        ReleaseArtifactError,
        RuntimeError,
    ) as exc:
        receipt["failure"] = _redact(str(exc), *redaction_paths)
    except subprocess.TimeoutExpired:
        receipt["failure"] = "La aceptación interna superó 1800 segundos"
    finally:
        if inner_receipt is not None:
            inner_receipt.unlink(missing_ok=True)
        try:
            receipt["cleanup"]["fixtures_removed"] = _remove_tree(
                fixture_root, parent=workspace_root
            )
            receipt["cleanup"]["installation_removed"] = _remove_tree(
                extraction_root, parent=workspace_root
            )
        except RuntimeError as exc:
            receipt["cleanup"]["failure"] = _redact(str(exc), *redaction_paths)
            exit_code = 1
        cleanup_ok = all(
            receipt["cleanup"].get(key) is True
            for key in ("fixtures_removed", "installation_removed")
        )
        package_steps = (
            receipt.get("package_acceptance", {}).get("steps", [])
            if isinstance(receipt.get("package_acceptance"), dict)
            else []
        )
        receipt["steps"] = [
            {
                "name": "archive_verified",
                "ok": receipt["archive_verified"],
                "exit_code": 0 if receipt["archive_verified"] else 1,
                "duration_seconds": 0.0,
            },
            *package_steps,
            {
                "name": "fixtures_removed",
                "ok": receipt["cleanup"]["fixtures_removed"],
                "exit_code": 0 if receipt["cleanup"]["fixtures_removed"] else 1,
                "duration_seconds": 0.0,
            },
            {
                "name": "installation_removed",
                "ok": receipt["cleanup"]["installation_removed"],
                "exit_code": 0 if receipt["cleanup"]["installation_removed"] else 1,
                "duration_seconds": 0.0,
            },
        ]
        try:
            contract_result = _validate_required_steps(receipt)
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            contract_result = {
                "schema_version": "release_acceptance_contract_result_v1",
                "ok": False,
                "error": _redact(str(exc), *redaction_paths),
            }
        receipt["contract"] = contract_result
        receipt["ok"] = exit_code == 0 and cleanup_ok and contract_result["ok"]
        package_promotion = bool(
            isinstance(receipt.get("package_acceptance"), dict)
            and receipt["package_acceptance"].get("promotion_allowed") is True
        )
        receipt["promotion_allowed"] = bool(
            receipt["ok"]
            and receipt.get("archive", {}).get("promotion_allowed") is True
            and package_promotion
        )
        receipt = _redact_value(receipt, *redaction_paths)
        _write_receipt(receipt_path, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

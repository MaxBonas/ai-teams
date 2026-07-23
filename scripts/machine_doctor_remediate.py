from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.machine_doctor_receipt import (
    build_remediation_plan,
    validate_machine_doctor_receipt,
    write_explicit_receipt,
)
from aiteam.platform_runtime import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Crea un plan manual para una acción diagnosticada. "
            "No instala, autentica, modifica configuración ni ejecuta inferencias."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", type=Path)
    source.add_argument("--receipt", type=Path)
    parser.add_argument("--action", required=True)
    parser.add_argument("--subject")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Permite reemplazar únicamente el recibo indicado.",
    )
    args = parser.parse_args()

    source_path = args.receipt or args.report
    payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    if args.receipt is not None:
        validate_machine_doctor_receipt(payload)
        report = payload["report"]
    else:
        report = payload
    plan = build_remediation_plan(
        report,
        action_code=args.action,
        subject_id=args.subject,
    )
    if args.output is not None:
        write_explicit_receipt(args.output, plan, overwrite=args.force)
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

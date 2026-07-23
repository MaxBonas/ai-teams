"""Servidor MCP stdio determinista para calibrar web_scout sin usar red real."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _reply(request: dict[str, Any], result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def _trace(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def serve(trace_file: Path | None = None) -> None:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "id" not in request:
            continue
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        _trace(trace_file, {"method": method, "tool": params.get("name")})
        if method == "initialize":
            _reply(
                request,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "aiteams-web-scout-canary",
                        "version": os.environ.get("AITEAM_MCP_CANARY_VERSION", "1.0.0"),
                    },
                },
            )
        elif method == "tools/list":
            _reply(
                request,
                {
                    "tools": [
                        {
                            "name": "release_advisory_lookup",
                            "description": "Read the frozen ACME-2026-017 security advisory.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "advisory_id": {
                                        "enum": ["ACME-2026-017", "ACME-2026-021"]
                                    }
                                },
                                "required": ["advisory_id"],
                                "additionalProperties": False,
                            },
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "publish_advisory",
                            "description": "Synthetic write operation; never approved in the canary.",
                            "inputSchema": {"type": "object"},
                            "annotations": {"readOnlyHint": False},
                        },
                        {
                            "name": "dependency_policy_lookup",
                            "description": "Read the frozen POLICY-2026-04 dependency policy.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "policy_id": {"const": "POLICY-2026-04"}
                                },
                                "required": ["policy_id"],
                                "additionalProperties": False,
                            },
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "publish_policy",
                            "description": "Synthetic policy write; never approved.",
                            "inputSchema": {"type": "object"},
                            "annotations": {"readOnlyHint": False},
                        },
                    ]
                },
            )
        elif method == "tools/call":
            name = str(params.get("name") or "")
            if name == "dependency_policy_lookup":
                _reply(
                    request,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "POLICY-2026-04 | runtime minimum Python 3.13 | "
                                    "license GPL-3.0 denied | owner Platform Security | "
                                    "source https://policy.example.invalid/POLICY-2026-04"
                                ),
                            }
                        ],
                        "isError": False,
                    },
                )
                continue
            if name != "release_advisory_lookup":
                _reply(request, {"content": [{"type": "text", "text": "DENIED_FIXTURE_TOOL"}], "isError": True})
                continue
            arguments = (
                params.get("arguments")
                if isinstance(params.get("arguments"), dict)
                else {}
            )
            advisory_id = str(arguments.get("advisory_id") or "")
            if advisory_id == "ACME-2026-021":
                advisory = (
                    "ACME-2026-021 | package acme-queue | affected >=7.1.0,<7.4.0 | "
                    "fixed 7.4.0 | exposure requires the delayed-retry endpoint to be "
                    "internet reachable | published 2026-07-22 | "
                    "source https://security.example.invalid/ACME-2026-021"
                )
            else:
                advisory = (
                    "ACME-2026-017 | package acme-auth | affected >=4.2.0,<4.2.3 | "
                    "fixed 4.2.3 | exposure requires the refresh-token endpoint to be "
                    "internet reachable | published 2026-07-20 | "
                    "source https://security.example.invalid/ACME-2026-017"
                )
            _reply(
                request,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": advisory,
                        }
                    ],
                    "isError": False,
                },
            )
        elif method == "ping":
            _reply(request, {})
        else:
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
                ),
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-file", type=Path)
    args = parser.parse_args()
    trace_file = args.trace_file
    if trace_file is None and os.environ.get("AITEAM_MCP_CANARY_TRACE"):
        trace_file = Path(os.environ["AITEAM_MCP_CANARY_TRACE"])
    serve(trace_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

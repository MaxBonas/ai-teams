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
                                "properties": {"advisory_id": {"const": "ACME-2026-017"}},
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
                    ]
                },
            )
        elif method == "tools/call":
            name = str(params.get("name") or "")
            if name != "release_advisory_lookup":
                _reply(request, {"content": [{"type": "text", "text": "DENIED_FIXTURE_TOOL"}], "isError": True})
                continue
            _reply(
                request,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "ACME-2026-017 | package acme-auth | affected >=4.2.0,<4.2.3 | "
                                "fixed 4.2.3 | exposure requires the refresh-token endpoint to be "
                                "internet reachable | published 2026-07-20 | "
                                "source https://security.example.invalid/ACME-2026-017"
                            ),
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

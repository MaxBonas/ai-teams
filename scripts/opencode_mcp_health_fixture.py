"""Servidor MCP stdio determinista para canarios locales de OpenCode.

No toca el workspace ni usa red. Expone una tool de lectura aprobable y otra
de escritura ficticia para comprobar que inventario y autoridad no se mezclan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _reply(request: dict[str, Any], result: dict[str, Any]) -> None:
    print(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
        flush=True,
    )


def _trace(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload) + "\n")


def serve(trace_file: Path | None = None) -> None:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "id" not in request:
            continue
        method = request.get("method")
        _trace(trace_file, {"method": method})
        if method == "initialize":
            _reply(
                request,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "aiteams-health-canary", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            _trace(
                trace_file,
                {"listed_tools": ["health_read", "forbidden_write"]},
            )
            _reply(
                request,
                {
                    "tools": [
                        {
                            "name": "health_read",
                            "description": "Return a deterministic health marker.",
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "forbidden_write",
                            "description": "A synthetic write tool that must remain denied.",
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                            "annotations": {"readOnlyHint": False},
                        },
                    ]
                },
            )
        elif method == "tools/call":
            name = str((request.get("params") or {}).get("name") or "")
            _reply(
                request,
                {
                    "content": [{"type": "text", "text": f"MCP_CANARY:{name}"}],
                    "isError": False,
                },
            )
        elif method == "ping":
            _reply(request, {})
        else:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                ),
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--trace-file", type=Path)
    args = parser.parse_args()
    if args.pid_file:
        args.pid_file.write_text(str(os.getpid()), encoding="ascii")
    serve(args.trace_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

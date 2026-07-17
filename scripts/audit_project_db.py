"""Token-cheap audit report over a layer-2 project's .aiteam/aiteam.db.

Reads ONLY structured fields (statuses, error codes, costs, timestamps) plus
short excerpts for anomalous runs — never full transcripts. Intended for a
supervisor (human or LLM) that wants to judge project health from aggregates.

Usage:
    python scripts/audit_project_db.py "C:/Users/.../AI Teams Projects/CLI Notas"
    python scripts/audit_project_db.py <project_dir> --excerpts   # include stderr/stdout tails for failures
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

EXCERPT_LEN = 400

# La consola de Windows por defecto es cp1252: los caracteres del informe
# (·, →) salían como '?' — forzar UTF-8 en stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _connect(project_dir: Path) -> sqlite3.Connection:
    db = project_dir / ".aiteam" / "aiteam.db"
    if not db.exists():
        sys.exit(f"No aiteam.db under {project_dir}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def section(title: str) -> None:
    print(f"\n== {title} ==")


def report(project_dir: Path, *, excerpts: bool) -> None:
    conn = _connect(project_dir)
    q = conn.execute

    section("GOAL")
    for r in q("SELECT title, status, created_at FROM goals"):
        print(f"  [{r['status']}] {r['title']}  (created {r['created_at']})")

    section("ISSUES por status")
    for r in q("SELECT status, COUNT(*) n FROM issues GROUP BY status ORDER BY n DESC"):
        print(f"  {r['status']}: {r['n']}")

    section("RUNS por status")
    for r in q("SELECT status, COUNT(*) n FROM runs GROUP BY status ORDER BY n DESC"):
        print(f"  {r['status']}: {r['n']}")

    section("RUNS fallidas por error_code")
    for r in q(
        "SELECT error_code, COUNT(*) n FROM runs WHERE status='failed' "
        "GROUP BY error_code ORDER BY n DESC"
    ):
        print(f"  {r['error_code']}: {r['n']}")

    section("RUNS skipped por motivo")
    for r in q(
        "SELECT error_code, COUNT(*) n FROM runs WHERE status='skipped' "
        "GROUP BY error_code ORDER BY n DESC"
    ):
        print(f"  {r['error_code']}: {r['n']}")

    section("RUNS por agente/adapter")
    for r in q(
        "SELECT agent_id, adapter_type, model, COUNT(*) n, "
        "SUM(status='completed') ok, SUM(status='failed') ko FROM runs "
        "GROUP BY agent_id, adapter_type, model ORDER BY n DESC"
    ):
        print(f"  {r['agent_id']} · {r['adapter_type']}/{r['model']}: {r['n']} ({r['ok']} ok, {r['ko']} ko)")

    section("COSTE")
    r = q(
        "SELECT COALESCE(SUM(cost_cents),0) c, COALESCE(SUM(input_tokens),0) i, "
        "COALESCE(SUM(output_tokens),0) o FROM cost_events"
    ).fetchone()
    print(f"  total: {r['c'] / 100:.2f} USD  ({r['i']:,} in / {r['o']:,} out tokens)")
    for r in q(
        "SELECT channel, agent_id, COUNT(*) n, COALESCE(SUM(cost_cents),0) c, "
        "COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o "
        "FROM cost_events GROUP BY channel, agent_id ORDER BY i DESC"
    ):
        print(
            f"  {r['channel'] or '?'} · {r['agent_id']}: {r['n']} runs, "
            f"{r['c'] / 100:.2f} USD, {r['i']:,} in / {r['o']:,} out"
        )

    has_quorum = q(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('quorum_sessions','quorum_contributions')"
    ).fetchone()[0] == 2
    if has_quorum:
        section("QUORUM")
        rows = q(
            "SELECT status, COUNT(*) n FROM quorum_sessions GROUP BY status ORDER BY n DESC"
        ).fetchall()
        if rows:
            for row in rows:
                print(f"  {row['status']}: {row['n']}")
        else:
            print("  (sin sesiones)")
        linked = q(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN ce.id IS NOT NULL THEN 1 ELSE 0 END) with_cost "
            "FROM quorum_contributions qc LEFT JOIN cost_events ce ON ce.run_id=qc.run_id"
        ).fetchone()
        print(
            f"  contribuciones con cost_event: {int(linked['with_cost'] or 0)}/"
            f"{int(linked['total'] or 0)}"
        )

    section("SALUD DEL ROUTER (tasa de infra-fallos por proveedor)")
    infra_codes = (
        "api_error", "subscription_cli_not_found", "subscription_cli_nonzero_exit",
        "subscription_cli_timeout", "subscription_cli_error", "subscription_cli_parse_error",
        "liveness_timeout",
    )
    ph = ",".join("?" for _ in infra_codes)
    router_rows = q(
        f"SELECT provider, COUNT(*) n, "
        f"SUM(CASE WHEN status='failed' AND error_code IN ({ph}) THEN 1 ELSE 0 END) infra "
        f"FROM runs WHERE provider IS NOT NULL GROUP BY provider ORDER BY n DESC",
        infra_codes,
    ).fetchall()
    any_router = False
    for r in router_rows:
        if r["n"] < 5:
            continue
        any_router = True
        rate = (r["infra"] or 0) / r["n"]
        flag = "!! " if rate > 0.25 else "OK "
        print(f"  {flag}{r['provider']}: {rate:.0%} infra-fallos ({r['infra']}/{r['n']} runs)")
    if not any_router:
        print("  (sin proveedor con >=5 runs)")

    section("INTERACCIONES (escalaciones)")
    for r in q("SELECT status, COUNT(*) n FROM issue_thread_interactions GROUP BY status"):
        print(f"  {r['status']}: {r['n']}")
    lat = q(
        "SELECT COUNT(*) n, AVG((julianday(resolved_at)-julianday(created_at))*86400.0) avg_s, "
        "MAX((julianday(resolved_at)-julianday(created_at))*86400.0) max_s "
        "FROM issue_thread_interactions WHERE kind='request_confirmation' AND resolved_at IS NOT NULL"
    ).fetchone()
    if lat and lat["n"]:
        print(f"  latencia de decisión: media {lat['avg_s']/60:.1f} min, máx {lat['max_s']/60:.1f} min ({lat['n']} resueltas)")

    section("ULTIMA ACTIVIDAD")
    r = q("SELECT MAX(updated_at) t FROM runs").fetchone()
    print(f"  ultimo run actualizado: {r['t']}")
    for r in q(
        "SELECT substr(created_at,1,16) c, agent_id, status, error_code "
        "FROM runs ORDER BY created_at DESC LIMIT 8"
    ):
        err = f" err={r['error_code']}" if r["error_code"] else ""
        print(f"  {r['c']} {r['agent_id']} {r['status']}{err}")

    # ── Invariantes: cada hit es un bug o algo que investigar ────────────
    section("INVARIANTES")
    checks = {
        "runs 'running' de mas de 30 min (zombis)": (
            "SELECT COUNT(*) FROM runs WHERE status='running' "
            "AND started_at < datetime('now', '-30 minutes')"
        ),
        "wakeups 'running'/'claimed' sin run viva (huerfanos)": (
            "SELECT COUNT(*) FROM wakeup_requests w WHERE w.status IN ('running','claimed') "
            "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.wakeup_request_id = w.id "
            "AND r.status = 'running')"
        ),
        "issues in_progress sin actividad en 2h": (
            "SELECT COUNT(*) FROM issues WHERE status='in_progress' "
            "AND updated_at < datetime('now', '-2 hours')"
        ),
        "runs failed sin error_code": (
            "SELECT COUNT(*) FROM runs WHERE status='failed' AND error_code IS NULL"
        ),
        "interacciones pendientes de usuario": (
            "SELECT COUNT(*) FROM issue_thread_interactions "
            "WHERE status NOT IN ('resolved','cancelled','accepted','rejected')"
        ),
    }
    if has_quorum:
        checks.update(_quorum_invariant_checks())
    ok = True
    for label, sql in checks.items():
        n = q(sql).fetchone()[0]
        flag = "OK " if n == 0 else "!! "
        if n:
            ok = False
        print(f"  {flag}{label}: {n}")
    if ok:
        print("  (todos los invariantes en verde)")

    if excerpts:
        section("EXCERPTS de runs fallidas (tail)")
        for r in q(
            "SELECT id, agent_id, error_code, stderr_excerpt, stdout_excerpt "
            "FROM runs WHERE status='failed' ORDER BY created_at DESC LIMIT 5"
        ):
            tail = (r["stderr_excerpt"] or r["stdout_excerpt"] or "").strip()[-EXCERPT_LEN:]
            print(f"  -- {r['id']} ({r['agent_id']}, {r['error_code']}):")
            for line in tail.splitlines()[-6:]:
                print(f"     {line}")


def _quorum_invariant_checks() -> dict[str, str]:
    """Invariantes de quorum basados en rutas durables, no solo en status."""
    active_wakeup = "('queued','claimed','running')"
    active_run = "('queued','running')"
    open_interaction = "('accepted','rejected','answered','cancelled','expired')"
    return {
        "quorum reviewing sin auditor/run/wakeup vivo": f"""
            SELECT COUNT(*) FROM quorum_sessions qs
            WHERE qs.status='reviewing'
              AND NOT EXISTS (
                  SELECT 1 FROM issues child
                  WHERE json_extract(child.metadata_json, '$.quorum_session_id')=qs.id
                    AND child.status NOT IN ('done','cancelled','blocked')
                    AND (
                        EXISTS (SELECT 1 FROM runs r WHERE r.issue_id=child.id AND r.status IN {active_run})
                        OR EXISTS (
                            SELECT 1 FROM wakeup_requests w
                            WHERE w.status IN {active_wakeup}
                              AND COALESCE(json_extract(w.payload_json, '$.issue_id'), '')=child.id
                        )
                    )
              )
        """,
        "quorum ready sin wakeup/run de sintesis": f"""
            SELECT COUNT(*) FROM quorum_sessions qs
            WHERE qs.status='ready'
              AND NOT EXISTS (
                  SELECT 1 FROM wakeup_requests w
                  WHERE w.status IN {active_wakeup}
                    AND json_extract(w.payload_json, '$.quorum_session_id')=qs.id
                    AND w.reason='quorum_ready'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM runs r
                  WHERE r.issue_id=qs.issue_id AND r.status IN {active_run}
              )
        """,
        "quorum degraded sin escalado durable": f"""
            SELECT COUNT(*) FROM quorum_sessions qs
            WHERE qs.status='degraded'
              AND NOT EXISTS (
                  SELECT 1 FROM wakeup_requests w
                  WHERE w.status IN {active_wakeup}
                    AND json_extract(w.payload_json, '$.quorum_session_id')=qs.id
                    AND w.reason IN ('quorum_degraded','quorum_ready')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM issue_thread_interactions i
                  WHERE i.issue_id=qs.issue_id AND i.status NOT IN {open_interaction}
              )
        """,
        "quorum accepted con issue no terminal": """
            SELECT COUNT(*) FROM quorum_sessions qs
            JOIN issues i ON i.id=qs.issue_id
            WHERE qs.status='accepted' AND i.status NOT IN ('done','cancelled')
        """,
        "contribuciones quorum validas sin provenance completa": """
            SELECT COUNT(*) FROM quorum_contributions qc
            LEFT JOIN runs r ON r.id=qc.run_id
            WHERE qc.valid=1 AND (
                qc.run_id IS NULL OR r.id IS NULL
                OR COALESCE(qc.provider,'')=''
                OR COALESCE(qc.model,'')=''
                OR COALESCE(qc.channel,'')=''
            )
        """,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--excerpts", action="store_true", help="include failure excerpts")
    args = parser.parse_args()
    report(args.project_dir.resolve(), excerpts=args.excerpts)


if __name__ == "__main__":
    main()

import sqlite3
import json
from pathlib import Path
from typing import Any


class SqliteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS workflow_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS workflow_state_entries (
                    task_root TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            ''')

    def _get_conn(self) -> sqlite3.Connection:
        # isolation_level=None allows manual transaction management or auto-commit
        return sqlite3.connect(str(self.db_path), isolation_level=None, timeout=20.0)

    @staticmethod
    def _workflow_payload_with_task_root(task_root: str, payload: Any) -> dict[str, Any]:
        normalized = str(task_root or "").strip()
        base = dict(payload) if isinstance(payload, dict) else {}
        if normalized:
            base["task_root"] = normalized
        return base

    def load_all_tasks(self) -> list[dict]:
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            cursor = conn.execute("SELECT payload FROM tasks")
            return [json.loads(row[0]) for row in cursor.fetchall()]

    def upsert_tasks(self, tasks: list[dict]) -> None:
        if not tasks:
            return
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO tasks (task_id, payload) VALUES (?, ?)",
                    [
                        (str(task.get("task_id", "") or ""), json.dumps(task, ensure_ascii=False))
                        for task in tasks
                        if str(task.get("task_id", "") or "").strip()
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def delete_tasks(self, task_ids: list[str]) -> None:
        normalized = [str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()]
        if not normalized:
            return
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.executemany(
                    "DELETE FROM tasks WHERE task_id = ?",
                    [(task_id,) for task_id in normalized],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def save_all_tasks(self, tasks: list[dict]):
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.execute("DELETE FROM tasks")
                conn.executemany(
                    "INSERT INTO tasks (task_id, payload) VALUES (?, ?)",
                    [(t.get("task_id", ""), json.dumps(t, ensure_ascii=False)) for t in tasks]
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _load_workflow_entries(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT task_root, payload FROM workflow_state_entries"
        ).fetchall()
        if rows:
            state: dict[str, Any] = {}
            for task_root, payload in rows:
                try:
                    state[str(task_root)] = self._workflow_payload_with_task_root(
                        str(task_root),
                        json.loads(payload),
                    )
                except Exception:
                    continue
            return state

        legacy_row = conn.execute(
            "SELECT payload FROM workflow_state WHERE id = 1"
        ).fetchone()
        if not legacy_row:
            return {}
        try:
            legacy_payload = json.loads(legacy_row[0])
        except Exception:
            return {}
        if not isinstance(legacy_payload, dict):
            return {}
        normalized_state: dict[str, Any] = {}
        for task_root, payload in legacy_payload.items():
            if not str(task_root or "").strip() or not isinstance(payload, dict):
                continue
            normalized_state[str(task_root)] = self._workflow_payload_with_task_root(
                str(task_root),
                payload,
            )
        return normalized_state

    def load_workflow_state(self) -> dict[str, Any]:
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            return self._load_workflow_entries(conn)

    def load_workflow_entry(self, task_root: str) -> dict[str, Any]:
        normalized = str(task_root or "").strip()
        if not normalized:
            return {}
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            row = conn.execute(
                "SELECT payload FROM workflow_state_entries WHERE task_root = ?",
                (normalized,),
            ).fetchone()
            if row:
                try:
                    payload = json.loads(row[0])
                except Exception:
                    return {}
                if isinstance(payload, dict):
                    return self._workflow_payload_with_task_root(normalized, payload)
                return {}
            legacy = self._load_workflow_entries(conn)
            entry = legacy.get(normalized, {})
            if isinstance(entry, dict):
                return self._workflow_payload_with_task_root(normalized, entry)
            return {}

    def save_workflow_state(self, state: dict[str, Any]):
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.execute("DELETE FROM workflow_state_entries")
                conn.executemany(
                    "INSERT OR REPLACE INTO workflow_state_entries (task_root, payload) VALUES (?, ?)",
                    [
                        (
                            str(task_root),
                            json.dumps(
                                self._workflow_payload_with_task_root(str(task_root), payload),
                                ensure_ascii=False,
                            ),
                        )
                        for task_root, payload in state.items()
                        if str(task_root).strip() and isinstance(payload, dict)
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def save_workflow_entry(self, task_root: str, payload: dict[str, Any]) -> None:
        normalized = str(task_root or "").strip()
        if not normalized:
            return
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO workflow_state_entries (task_root, payload) VALUES (?, ?)",
                    (
                        normalized,
                        json.dumps(
                            self._workflow_payload_with_task_root(normalized, payload),
                            ensure_ascii=False,
                        ),
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def delete_workflow_entries(self, task_roots: list[str]) -> None:
        normalized = [str(task_root or "").strip() for task_root in task_roots if str(task_root or "").strip()]
        if not normalized:
            return
        import contextlib
        with contextlib.closing(self._get_conn()) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                conn.executemany(
                    "DELETE FROM workflow_state_entries WHERE task_root = ?",
                    [(task_root,) for task_root in normalized],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

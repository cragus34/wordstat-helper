"""
Хранилище запросов и истории в SQLite.
Поддерживает немедленное сохранение и обновление промежуточных результатов.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Создаёт или обновляет структуру БД."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS queries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                seed_phrase  TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'completed',
                phrase_count INTEGER NOT NULL DEFAULT 0,
                results_json TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # Миграция: если колонка status отсутствует в существующей базе
        cur = con.execute("PRAGMA table_info(queries)")
        columns = [row["name"] for row in cur.fetchall()]
        if "status" not in columns:
            con.execute("ALTER TABLE queries ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")


def create_query(seed_phrase: str) -> int:
    """Создаёт запрос в БД в статусе running сразу на старте."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO queries (seed_phrase, created_at, status, phrase_count, results_json)
               VALUES (?, ?, 'running', 0, '[]')""",
            (
                seed_phrase,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def update_query_progress(query_id: int, results: list[dict], status: str = "running"):
    """Обновляет промежуточные результаты прямо в процессе сбора."""
    with _conn() as con:
        con.execute(
            """UPDATE queries
               SET phrase_count = ?, results_json = ?, status = ?
               WHERE id = ?""",
            (
                len(results),
                json.dumps(results, ensure_ascii=False),
                status,
                query_id,
            ),
        )


def finish_query(query_id: int, results: list[dict], status: str = "completed"):
    """Окончательно сохраняет результаты запроса."""
    update_query_progress(query_id, results, status=status)


def list_queries() -> list[dict]:
    """Возвращает все запросы (только метаданные)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT id, seed_phrase, created_at, status, phrase_count FROM queries ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_query(query_id: int) -> dict | None:
    """Возвращает один запрос вместе с результатами."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM queries WHERE id = ?", (query_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["results"] = json.loads(d.pop("results_json"))
    return d

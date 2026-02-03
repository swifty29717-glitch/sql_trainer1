import sqlite3
import threading
from pathlib import Path
from collections import Counter, OrderedDict

APP_DB = Path("data/app.db")
DATASETS_DIR = Path("data/datasets")

# Fixed difficulty order for UI
LEVELS_ORDER = ["Easy", "Medium", "Hard", "Advanced"]

# Один lock на процесс: защищает от одновременного создания dataset-файлов в разных потоках Flask
_DATASET_CREATE_LOCK = threading.Lock()


def connect_app_db():
    """Connection to catalog DB (tasks list)."""
    conn = sqlite3.connect(APP_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_app_db(schema_path: Path = Path("schema.sql")):
    """
    Create and seed app.db if it doesn't exist yet.
    Safe for local pet project: we run schema.sql once when app.db is missing.
    """
    if APP_DB.exists():
        return

    APP_DB.parent.mkdir(parents=True, exist_ok=True)
    sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(APP_DB)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def get_levels():
    """Return difficulty levels in desired order."""
    return LEVELS_ORDER[:]


def get_tasks(level: str | None = None):
    """Get tasks list. If level is provided, filter by level."""
    sql = "SELECT id, title, short_desc, level FROM tasks"
    params = []

    if level and level in LEVELS_ORDER:
        sql += " WHERE level = ?"
        params.append(level)

    sql += " ORDER BY id"

    with connect_app_db() as conn:
        return conn.execute(sql, params).fetchall()


def get_task(task_id: int):
    with connect_app_db() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def dataset_path(task_id: int) -> Path:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    return DATASETS_DIR / f"task_{task_id}.db"


def _safe_executescript(conn: sqlite3.Connection, sql: str):
    """
    Страховка:
    - добавляем ведущий ';' чтобы WITH/INSERT не "прилипали" к предыдущей команде,
      если где-то забыли ';'
    """
    sql = sql or ""
    conn.executescript(";\n" + sql)


def ensure_dataset(task_row):
    """
    Create dataset DB for a task if missing.

    Fixes:
    - lock (защита от одновременного создания одной и той же БД)
    - создание в temp-файл и атомарный replace -> никогда не будет "полусозданной" БД
    """
    import sqlite3
import threading
from pathlib import Path

_DATASET_CREATE_LOCK = threading.Lock()

def dataset_path(task_id: int) -> Path:
    Path("data/datasets").mkdir(parents=True, exist_ok=True)
    return Path("data/datasets") / f"task_{task_id}.db"

def ensure_dataset(task_row):
    task_id = int(task_row["id"])
    final_path = dataset_path(task_id)

    if final_path.exists():
        return

    with _DATASET_CREATE_LOCK:
        if final_path.exists():
            return

        tmp_path = final_path.with_suffix(".db.tmp")
        if tmp_path.exists():
            tmp_path.unlink()

        conn = sqlite3.connect(tmp_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA foreign_keys = ON;")

            dataset_sql = (task_row["dataset_sql"] or "").strip()
            seed_sql = (task_row["seed_sql"] or "").strip()

            # Один executescript, транзакция внутри него.
            # Ведущий ";\n" — страховка, если где-то забыли ';' перед WITH/INSERT.
            script = (
                "BEGIN IMMEDIATE;\n"
                ";\n" + dataset_sql + "\n"
                ";\n" + seed_sql + "\n"
                ";\nCOMMIT;\n"
            )

            conn.executescript(script)

        except Exception:
            # rollback безопаснее через API (если транзакции нет — обычно просто проигнорируется)
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        tmp_path.replace(final_path)



def reset_dataset(task_row):
    """Recreate dataset DB from scratch."""
    task_id = int(task_row["id"])
    path = dataset_path(task_id)

    with _DATASET_CREATE_LOCK:
        if path.exists():
            path.unlink()
        ensure_dataset(task_row)


def _strip_leading_comments(sql: str) -> str:
    s = sql.lstrip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl == -1 else s[nl + 1 :].lstrip()
            continue
        if s.startswith("/*"):
            end = s.find("*/")
            if end == -1:
                return s
            s = s[end + 2 :].lstrip()
            continue
        break
    return s


def _tokenize_sql(sql: str) -> list[str]:
    """Very small tokenizer for validation (not a full SQL parser)."""
    tokens = []
    s = sql
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]

        if ch.isspace():
            i += 1
            continue

        if ch == "-" and i + 1 < n and s[i + 1] == "-":
            j = s.find("\n", i + 2)
            i = n if j == -1 else j + 1
            continue

        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue

        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if s[i] == quote:
                    if i + 1 < n and s[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(s[i:j].lower())
            i = j
            continue

        i += 1

    return tokens


def validate_select_only(sql: str) -> None:
    """
    Enforce SELECT-only policy:
    - allow: SELECT ... ; WITH ... SELECT ...
    - disallow: INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/PRAGMA/etc.
    """
    s = _strip_leading_comments(sql)
    tokens = _tokenize_sql(s)
    if not tokens:
        raise ValueError("Введите SQL-запрос.")

    first = tokens[0]
    if first == "select":
        return

    if first == "with":
        forbidden = {"insert", "update", "delete", "replace", "create", "drop", "alter", "pragma", "vacuum", "attach", "detach"}
        allowed = {"select"}
        for t in tokens[1:]:
            if t in forbidden:
                raise ValueError("Разрешены только SELECT-запросы (запрещены изменения данных и структуры).")
            if t in allowed:
                return
        raise ValueError("Разрешены только SELECT-запросы (WITH должен заканчиваться SELECT).")

    raise ValueError("Разрешены только SELECT-запросы (запрещены изменения данных и структуры).")


def run_user_sql(task_id: int, sql: str, limit: int = 500):
    """
    Execute SQL against dataset DB of given task.
    Returns (columns, rows) for SELECT-like queries, otherwise (None, None).
    """
    path = dataset_path(task_id)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cleaned = sql.strip()
        validate_select_only(cleaned)

        if ";" in cleaned.rstrip(";"):
            raise ValueError("Пожалуйста, выполните один SQL-запрос без нескольких команд через ';'.")

        cur.execute(cleaned)

        if cur.description is not None:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
            return cols, rows

        conn.commit()
        return None, None
    finally:
        conn.close()


def compare_results(user_cols, user_rows, sol_cols, sol_rows, mode: str):
    """
    Compare user's result to solution result.
    - Column names must match (case-insensitive).
    - Data compare:
        ordered   -> exact row order matters
        unordered -> order doesn't matter (multiset compare, duplicates are counted)
    Returns: (is_ok: bool, message: str)
    """
    if user_cols is None or sol_cols is None:
        return False, "Запрос должен возвращать табличный результат (SELECT/WITH)."

    if [c.lower() for c in user_cols] != [c.lower() for c in sol_cols]:
        return False, f"Колонки отличаются. Ожидалось: {sol_cols}"

    user_tuples = [tuple(r[c] for c in user_cols) for r in user_rows]
    sol_tuples = [tuple(r[c] for c in sol_cols) for r in sol_rows]

    mode = (mode or "unordered").lower()
    if mode == "ordered":
        ok = user_tuples == sol_tuples
        return ok, ("Совпадает." if ok else "Значения/порядок строк не совпадают с ожидаемыми.")
    else:
        ok = Counter(user_tuples) == Counter(sol_tuples)
        return ok, ("Совпадает (порядок строк не важен)." if ok else "Значения не совпадают с ожидаемыми.")


def get_sample_data(task_id: int, limit: int = 5):
    db_path = f"data/datasets/task_{task_id}.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [r["name"] for r in cur.fetchall()]

    result = OrderedDict()

    for table in tables:
        try:
            cur.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            result[table] = {"columns": columns, "rows": rows}
        except sqlite3.Error:
            continue

    conn.close()
    return result

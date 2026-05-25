"""db.py — SQLite persistence for Prompt Optimization Lab."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "lab.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS use_cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    trigger     TEXT NOT NULL DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requirements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    content     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL DEFAULT 1,
    content     TEXT NOT NULL DEFAULT '',
    is_current  INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sub_agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    key_name    TEXT NOT NULL,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tools (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id   INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    return_schema TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rich_media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    slug        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tests (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id                 INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    test_id                     TEXT NOT NULL,
    name                        TEXT NOT NULL,
    category                    TEXT NOT NULL DEFAULT 'happy_path',
    conversation_script         TEXT NOT NULL DEFAULT '[]',
    pass_criteria               TEXT NOT NULL DEFAULT '[]',
    agent_behavior_expectations TEXT NOT NULL DEFAULT '[]',
    setup_notes                 TEXT DEFAULT '',
    mock_overrides              TEXT DEFAULT '{}',
    created_at                  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id       INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    mode              TEXT NOT NULL DEFAULT 'auto',
    status            TEXT NOT NULL DEFAULT 'running',
    total_tests       INTEGER DEFAULT 0,
    current_pass      INTEGER DEFAULT 0,
    current_iteration INTEGER DEFAULT 0,
    max_iterations    INTEGER DEFAULT 10,
    started_at        TEXT DEFAULT (datetime('now')),
    ended_at          TEXT
);

CREATE TABLE IF NOT EXISTS iterations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    n            INTEGER NOT NULL,
    prompt_text  TEXT NOT NULL,
    results      TEXT NOT NULL DEFAULT '[]',
    passed       INTEGER DEFAULT 0,
    total        INTEGER DEFAULT 0,
    diagnosis    TEXT DEFAULT '',
    new_prompt   TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playwright_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_ids  TEXT NOT NULL DEFAULT '[]',
    mode          TEXT NOT NULL DEFAULT 'headless',
    status        TEXT NOT NULL DEFAULT 'running',
    bot_id        TEXT NOT NULL DEFAULT '',
    total_tests   INTEGER DEFAULT 0,
    passed        INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    started_at    TEXT DEFAULT (datetime('now')),
    ended_at      TEXT
);

CREATE TABLE IF NOT EXISTS playwright_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES playwright_runs(id) ON DELETE CASCADE,
    use_case_id      INTEGER,
    use_case_name    TEXT DEFAULT '',
    test_id          TEXT NOT NULL,
    name             TEXT NOT NULL,
    turns            TEXT NOT NULL DEFAULT '[]',
    overall          TEXT NOT NULL DEFAULT 'pending',
    summary          TEXT DEFAULT '',
    -- LLM-judge verdict details so the "Pass Criteria" / "Agent Behavior" /
    -- per-turn badges survive page reloads and past-run navigation.
    turn_verdicts    TEXT NOT NULL DEFAULT '[]',
    criteria_results TEXT NOT NULL DEFAULT '[]',
    behavior_results TEXT NOT NULL DEFAULT '[]',
    pass_criteria    TEXT NOT NULL DEFAULT '[]',
    behavior_expectations TEXT NOT NULL DEFAULT '[]',
    category         TEXT DEFAULT '',
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS acceptance_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id     INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    source          TEXT DEFAULT '',           -- "api" | "playwright"
    source_run_id   INTEGER,
    source_test_id  TEXT DEFAULT '',
    criterion       TEXT DEFAULT '',           -- the failing criterion/expected
    llm_reason      TEXT DEFAULT '',           -- what the LLM said was wrong
    actual_response TEXT DEFAULT '',           -- what the bot actually said/showed
    human_rule      TEXT NOT NULL,             -- short rule the LLM will read
    scope           TEXT NOT NULL DEFAULT 'agent',  -- "agent" | "test"
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_acceptance_rules_uc
    ON acceptance_rules(use_case_id, active);

CREATE TABLE IF NOT EXISTS custom_tests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    test_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    steps       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS custom_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    use_case_id   INTEGER NOT NULL REFERENCES use_cases(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'running',
    bot_id        TEXT NOT NULL DEFAULT '',
    total_tests   INTEGER DEFAULT 0,
    passed        INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    started_at    TEXT DEFAULT (datetime('now')),
    ended_at      TEXT
);

CREATE TABLE IF NOT EXISTS custom_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES custom_runs(id) ON DELETE CASCADE,
    use_case_id      INTEGER,
    use_case_name    TEXT DEFAULT '',
    test_id          TEXT NOT NULL,
    name             TEXT NOT NULL,
    turns            TEXT NOT NULL DEFAULT '[]',
    overall          TEXT NOT NULL DEFAULT 'pending',
    summary          TEXT DEFAULT '',
    created_at       TEXT DEFAULT (datetime('now'))
);
"""

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(seed_dir: Path | None = None):
    with db() as conn:
        conn.executescript(SCHEMA)
        # Migrations: add columns/tables that may not exist in older DBs
        for stmt in [
            "ALTER TABLE prompts ADD COLUMN label TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE use_cases ADD COLUMN trigger TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tests ADD COLUMN category TEXT NOT NULL DEFAULT 'happy_path'",
            "ALTER TABLE tests ADD COLUMN agent_behavior_expectations TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE tests ADD COLUMN setup_notes TEXT DEFAULT ''",
            # Persist LLM judge details so the criteria block survives reloads
            "ALTER TABLE playwright_results ADD COLUMN turn_verdicts TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE playwright_results ADD COLUMN criteria_results TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE playwright_results ADD COLUMN behavior_results TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE playwright_results ADD COLUMN pass_criteria TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE playwright_results ADD COLUMN behavior_expectations TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE playwright_results ADD COLUMN category TEXT DEFAULT ''",
            # Store full conversation turns with acceptance rules
            "ALTER TABLE acceptance_rules ADD COLUMN conversation_turns TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE runs ADD COLUMN model TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass


# ── Use cases ─────────────────────────────────────────────────────────────────

def list_use_cases() -> list[dict]:
    """Return all use cases with summary metadata for the management UI."""
    with db() as conn:
        rows = conn.execute("""
            SELECT uc.id, uc.name, uc.created_at, uc.updated_at,
                   (SELECT COUNT(*) FROM tests t WHERE t.use_case_id = uc.id) AS test_count,
                   (SELECT COUNT(*) FROM tools  tl WHERE tl.use_case_id = uc.id) AS tool_count,
                   (SELECT COUNT(*) FROM runs   r  WHERE r.use_case_id  = uc.id) AS run_count,
                   (SELECT version FROM prompts p WHERE p.use_case_id = uc.id AND p.is_current = 1) AS prompt_version,
                   (SELECT length(content) FROM prompts p WHERE p.use_case_id = uc.id AND p.is_current = 1) AS prompt_chars
            FROM use_cases uc
            ORDER BY uc.id
        """).fetchall()

        result = []
        for row in rows:
            uc = dict(row)
            last_run = conn.execute("""
                SELECT current_pass, total_tests, status, ended_at
                FROM runs WHERE use_case_id = ? ORDER BY id DESC LIMIT 1
            """, (uc["id"],)).fetchone()
            if last_run and last_run["total_tests"]:
                uc["last_pass"]   = last_run["current_pass"]
                uc["last_total"]  = last_run["total_tests"]
                uc["last_status"] = last_run["status"]
                uc["last_run_at"] = last_run["ended_at"]
            else:
                uc["last_pass"] = uc["last_total"] = None
                uc["last_status"] = uc["last_run_at"] = None
            result.append(uc)
        return result


def name_exists(name: str, exclude_id: int | None = None) -> bool:
    """Case-insensitive duplicate-name check."""
    with db() as conn:
        if exclude_id is None:
            row = conn.execute(
                "SELECT 1 FROM use_cases WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM use_cases WHERE LOWER(name) = LOWER(?) AND id != ? LIMIT 1",
                (name, exclude_id),
            ).fetchone()
        return row is not None


def create_use_case(name: str) -> int:
    with db() as conn:
        cur = conn.execute("INSERT INTO use_cases (name) VALUES (?)", (name,))
        uc_id = cur.lastrowid
        conn.execute("INSERT INTO requirements (use_case_id, content) VALUES (?, ?)", (uc_id, ""))
        conn.execute("INSERT INTO prompts (use_case_id, version, content, is_current) VALUES (?, 1, ?, 1)", (uc_id, ""))
        return uc_id


def rename_use_case(uc_id: int, name: str):
    with db() as conn:
        conn.execute("UPDATE use_cases SET name = ?, updated_at = datetime('now') WHERE id = ?", (name, uc_id))


def save_trigger(uc_id: int, trigger: str):
    with db() as conn:
        conn.execute("UPDATE use_cases SET trigger = ?, updated_at = datetime('now') WHERE id = ?", (trigger, uc_id))


def delete_use_case(uc_id: int):
    with db() as conn:
        conn.execute("DELETE FROM use_cases WHERE id = ?", (uc_id,))


def get_use_case(uc_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM use_cases WHERE id = ?", (uc_id,)).fetchone()
        if not row:
            return None
        uc = dict(row)

        req = conn.execute("SELECT content FROM requirements WHERE use_case_id = ?", (uc_id,)).fetchone()
        uc["requirements"] = req["content"] if req else ""

        prompt = conn.execute(
            "SELECT id, version, content, created_at FROM prompts WHERE use_case_id = ? AND is_current = 1", (uc_id,)
        ).fetchone()
        uc["prompt"] = dict(prompt) if prompt else {"id": None, "version": 0, "content": ""}

        uc["sub_agents"] = [dict(r) for r in conn.execute(
            "SELECT id, name, description FROM sub_agents WHERE use_case_id = ? ORDER BY id", (uc_id,)
        ).fetchall()]

        uc["memory_keys"] = [dict(r) for r in conn.execute(
            "SELECT id, key_name, description FROM memory_keys WHERE use_case_id = ? ORDER BY id", (uc_id,)
        ).fetchall()]

        uc["rich_media"] = [dict(r) for r in conn.execute(
            "SELECT id, name, slug FROM rich_media WHERE use_case_id = ? ORDER BY id", (uc_id,)
        ).fetchall()]

        tools = []
        for r in conn.execute("SELECT id, name, description, return_schema FROM tools WHERE use_case_id = ? ORDER BY id", (uc_id,)).fetchall():
            t = dict(r)
            try:
                t["return_schema"] = json.loads(t["return_schema"])
            except Exception:
                t["return_schema"] = {}
            tools.append(t)
        uc["tools"] = tools

        return uc


# ── Requirements ──────────────────────────────────────────────────────────────

def update_requirements(uc_id: int, content: str):
    with db() as conn:
        conn.execute("UPDATE requirements SET content = ? WHERE use_case_id = ?", (content, uc_id))
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


# ── Prompts ───────────────────────────────────────────────────────────────────

def save_prompt(uc_id: int, content: str, create_version: bool = False, label: str = "") -> dict:
    with db() as conn:
        if create_version:
            row = conn.execute("SELECT MAX(version) as mv FROM prompts WHERE use_case_id = ?", (uc_id,)).fetchone()
            next_v = (row["mv"] or 0) + 1
            conn.execute("UPDATE prompts SET is_current = 0 WHERE use_case_id = ?", (uc_id,))
            cur = conn.execute(
                "INSERT INTO prompts (use_case_id, version, content, is_current, label) VALUES (?, ?, ?, 1, ?)",
                (uc_id, next_v, content, label)
            )
            return {"id": cur.lastrowid, "version": next_v}
        else:
            row = conn.execute("SELECT id, version FROM prompts WHERE use_case_id = ? AND is_current = 1", (uc_id,)).fetchone()
            if row:
                conn.execute("UPDATE prompts SET content = ?, label = ? WHERE id = ?", (content, label, row["id"]))
                return {"id": row["id"], "version": row["version"]}
            else:
                cur = conn.execute("INSERT INTO prompts (use_case_id, version, content, is_current, label) VALUES (?, 1, ?, 1, ?)", (uc_id, content, label))
                return {"id": cur.lastrowid, "version": 1}


def get_prompt_versions(uc_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, version, is_current, created_at, label, substr(content,1,100) as preview FROM prompts WHERE use_case_id = ? ORDER BY version DESC",
            (uc_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_prompt_by_version(uc_id: int, version: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT id, version, content FROM prompts WHERE use_case_id = ? AND version = ?", (uc_id, version)).fetchone()
        return dict(row) if row else None


# ── Variables & Tools ─────────────────────────────────────────────────────────

def replace_variables(uc_id: int, sub_agents: list[dict], memory_keys: list[dict]):
    with db() as conn:
        conn.execute("DELETE FROM sub_agents WHERE use_case_id = ?", (uc_id,))
        for sa in sub_agents:
            conn.execute("INSERT INTO sub_agents (use_case_id, name, description) VALUES (?, ?, ?)",
                         (uc_id, sa["name"], sa.get("description", "")))
        conn.execute("DELETE FROM memory_keys WHERE use_case_id = ?", (uc_id,))
        for mk in memory_keys:
            conn.execute("INSERT INTO memory_keys (use_case_id, key_name, description) VALUES (?, ?, ?)",
                         (uc_id, mk["key_name"], mk.get("description", "")))
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


def replace_tools(uc_id: int, tools: list[dict]):
    with db() as conn:
        conn.execute("DELETE FROM tools WHERE use_case_id = ?", (uc_id,))
        for t in tools:
            schema = t.get("return_schema", {})
            conn.execute("INSERT INTO tools (use_case_id, name, description, return_schema) VALUES (?, ?, ?, ?)",
                         (uc_id, t["name"], t.get("description", ""),
                          json.dumps(schema) if isinstance(schema, dict) else schema))
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


def replace_rich_media(uc_id: int, items: list[dict]):
    with db() as conn:
        conn.execute("DELETE FROM rich_media WHERE use_case_id = ?", (uc_id,))
        for rm in items:
            conn.execute("INSERT INTO rich_media (use_case_id, name, slug) VALUES (?, ?, ?)",
                         (uc_id, rm["name"], rm.get("slug", "")))
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


# ── Tests ─────────────────────────────────────────────────────────────────────

def get_tests(uc_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tests WHERE use_case_id = ? ORDER BY test_id", (uc_id,)).fetchall()
        result = []
        for r in rows:
            t = dict(r)
            t["conversation_script"] = json.loads(t["conversation_script"])
            t["pass_criteria"]       = json.loads(t["pass_criteria"])
            t["mock_overrides"]      = json.loads(t["mock_overrides"] or "{}")
            try:
                t["agent_behavior_expectations"] = json.loads(t.get("agent_behavior_expectations") or "[]")
            except Exception:
                t["agent_behavior_expectations"] = []
            t.setdefault("category", "happy_path")
            t.setdefault("setup_notes", "")
            result.append(t)
        return result


def replace_tests(uc_id: int, tests: list[dict]):
    with db() as conn:
        conn.execute("DELETE FROM tests WHERE use_case_id = ?", (uc_id,))
        for t in tests:
            conn.execute(
                "INSERT INTO tests (use_case_id, test_id, name, category, conversation_script, pass_criteria, agent_behavior_expectations, setup_notes, mock_overrides) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uc_id, t["test_id"], t["name"],
                 t.get("category", "happy_path"),
                 json.dumps(t.get("conversation_script", [])),
                 json.dumps(t.get("pass_criteria", [])),
                 json.dumps(t.get("agent_behavior_expectations", [])),
                 t.get("setup_notes", ""),
                 json.dumps(t.get("mock_overrides", {})))
            )
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


# ── Runs & Iterations ─────────────────────────────────────────────────────────

def create_run(uc_id: int, mode: str, total_tests: int, max_iterations: int = 10, model: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO runs (use_case_id, mode, status, total_tests, max_iterations, model) VALUES (?, ?, 'running', ?, ?, ?)",
            (uc_id, mode, total_tests, max_iterations, model or "")
        )
        return cur.lastrowid


def update_run(run_id: int, **kwargs):
    if not kwargs:
        return
    with db() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*kwargs.values(), run_id))


def get_run(run_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(uc_id: int) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE use_case_id = ? ORDER BY id DESC", (uc_id,)
        ).fetchall()]


def save_iteration(run_id: int, n: int, prompt_text: str, results: list,
                   passed: int, total: int, diagnosis: str = "", new_prompt: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO iterations (run_id, n, prompt_text, results, passed, total, diagnosis, new_prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, n, prompt_text, json.dumps(results), passed, total, diagnosis, new_prompt)
        )
        return cur.lastrowid


def get_iterations(run_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM iterations WHERE run_id = ? ORDER BY n", (run_id,)).fetchall()
        result = []
        for r in rows:
            it = dict(r)
            it["results"] = json.loads(it["results"])
            result.append(it)
        return result


# ── Playwright runs ───────────────────────────────────────────────────────────

def create_playwright_run(use_case_ids: list, mode: str, bot_id: str, total_tests: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO playwright_runs (use_case_ids, mode, bot_id, total_tests) VALUES (?, ?, ?, ?)",
            (json.dumps(use_case_ids), mode, bot_id, total_tests),
        )
        return cur.lastrowid


def update_playwright_run(run_id: int, **kwargs):
    if not kwargs:
        return
    with db() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE playwright_runs SET {sets} WHERE id = ?", (*kwargs.values(), run_id))


def get_playwright_run(run_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM playwright_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["use_case_ids"] = json.loads(r.get("use_case_ids", "[]"))
        return r


def list_playwright_runs(limit: int = 30) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM playwright_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["use_case_ids"] = json.loads(r.get("use_case_ids", "[]"))
            result.append(r)
        return result


def save_playwright_result(run_id: int, use_case_id, use_case_name: str,
                            test_id: str, name: str, turns: list,
                            overall: str, summary: str,
                            turn_verdicts: list | None = None,
                            criteria_results: list | None = None,
                            behavior_results: list | None = None,
                            pass_criteria: list | None = None,
                            behavior_expectations: list | None = None,
                            category: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO playwright_results "
            "(run_id, use_case_id, use_case_name, test_id, name, turns, "
            " overall, summary, turn_verdicts, criteria_results, "
            " behavior_results, pass_criteria, behavior_expectations, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, use_case_id, use_case_name, test_id, name,
             json.dumps(turns), overall, summary,
             json.dumps(turn_verdicts or []),
             json.dumps(criteria_results or []),
             json.dumps(behavior_results or []),
             json.dumps(pass_criteria or []),
             json.dumps(behavior_expectations or []),
             category),
        )
        return cur.lastrowid


def get_playwright_results(run_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM playwright_results WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            for k in ("turns", "turn_verdicts", "criteria_results",
                      "behavior_results", "pass_criteria",
                      "behavior_expectations"):
                try:
                    r[k] = json.loads(r.get(k) or "[]")
                except Exception:
                    r[k] = []
            result.append(r)
        return result


def delete_playwright_run(run_id: int):
    with db() as conn:
        conn.execute("DELETE FROM playwright_runs WHERE id = ?", (run_id,))


# ── Acceptance rules ──────────────────────────────────────────────────────────

def list_acceptance_rules(uc_id: int, active_only: bool = False) -> list[dict]:
    with db() as conn:
        q = "SELECT * FROM acceptance_rules WHERE use_case_id = ?"
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(q, (uc_id,)).fetchall()]


def create_acceptance_rule(uc_id: int, human_rule: str,
                            criterion: str = "", llm_reason: str = "",
                            actual_response: str = "", scope: str = "agent",
                            source: str = "", source_run_id: int | None = None,
                            source_test_id: str = "",
                            conversation_turns: str = "[]") -> int:
    if scope not in ("agent", "test"):
        scope = "agent"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO acceptance_rules "
            "(use_case_id, source, source_run_id, source_test_id, criterion, "
            " llm_reason, actual_response, human_rule, scope, active, conversation_turns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (uc_id, source, source_run_id, source_test_id, criterion,
             llm_reason, actual_response, human_rule, scope, conversation_turns),
        )
        return cur.lastrowid


def update_acceptance_rule(rule_id: int, **kwargs):
    allowed = {"human_rule", "scope", "active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    with db() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE acceptance_rules SET {sets} WHERE id = ?",
                     (*fields.values(), rule_id))


def delete_acceptance_rule(rule_id: int):
    with db() as conn:
        conn.execute("DELETE FROM acceptance_rules WHERE id = ?", (rule_id,))


def get_acceptance_rule(rule_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM acceptance_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Custom tests ─────────────────────────────────────────────────────────────

def list_custom_tests(uc_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_tests WHERE use_case_id = ? ORDER BY test_id", (uc_id,)
        ).fetchall()
        result = []
        for r in rows:
            t = dict(r)
            t["steps"] = json.loads(t["steps"])
            result.append(t)
        return result


def save_custom_tests(uc_id: int, tests: list[dict]):
    with db() as conn:
        conn.execute("DELETE FROM custom_tests WHERE use_case_id = ?", (uc_id,))
        for t in tests:
            conn.execute(
                "INSERT INTO custom_tests (use_case_id, test_id, name, description, steps) "
                "VALUES (?, ?, ?, ?, ?)",
                (uc_id, t["test_id"], t["name"], t.get("description", ""),
                 json.dumps(t.get("steps", [])))
            )
        conn.execute("UPDATE use_cases SET updated_at = datetime('now') WHERE id = ?", (uc_id,))


def add_custom_test(uc_id: int, test_id: str, name: str, description: str, steps: list) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO custom_tests (use_case_id, test_id, name, description, steps) "
            "VALUES (?, ?, ?, ?, ?)",
            (uc_id, test_id, name, description, json.dumps(steps))
        )
        return cur.lastrowid


def update_custom_test(test_db_id: int, name: str, description: str, steps: list):
    with db() as conn:
        conn.execute(
            "UPDATE custom_tests SET name = ?, description = ?, steps = ? WHERE id = ?",
            (name, description, json.dumps(steps), test_db_id)
        )


def delete_custom_test(test_db_id: int):
    with db() as conn:
        conn.execute("DELETE FROM custom_tests WHERE id = ?", (test_db_id,))


# ── Custom runs ──────────────────────────────────────────────────────────────

def create_custom_run(uc_id: int, bot_id: str, total_tests: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO custom_runs (use_case_id, bot_id, total_tests) VALUES (?, ?, ?)",
            (uc_id, bot_id, total_tests),
        )
        return cur.lastrowid


def update_custom_run(run_id: int, **kwargs):
    if not kwargs:
        return
    with db() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE custom_runs SET {sets} WHERE id = ?", (*kwargs.values(), run_id))


def get_custom_run(run_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM custom_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_custom_runs(uc_id: int | None = None, limit: int = 30) -> list[dict]:
    with db() as conn:
        if uc_id:
            rows = conn.execute(
                "SELECT * FROM custom_runs WHERE use_case_id = ? ORDER BY id DESC LIMIT ?",
                (uc_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM custom_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def save_custom_result(run_id: int, use_case_id: int, use_case_name: str,
                       test_id: str, name: str, turns: list,
                       overall: str, summary: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO custom_results "
            "(run_id, use_case_id, use_case_name, test_id, name, turns, overall, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, use_case_id, use_case_name, test_id, name,
             json.dumps(turns), overall, summary),
        )
        return cur.lastrowid


def get_custom_results(run_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_results WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["turns"] = json.loads(r.get("turns") or "[]")
            result.append(r)
        return result


def update_custom_result(result_id: int, **kwargs):
    if not kwargs:
        return
    with db() as conn:
        # JSON-encode turns if present
        if "turns" in kwargs and isinstance(kwargs["turns"], list):
            kwargs["turns"] = json.dumps(kwargs["turns"])
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE custom_results SET {sets} WHERE id = ?", (*kwargs.values(), result_id))


def delete_custom_run(run_id: int):
    with db() as conn:
        conn.execute("DELETE FROM custom_runs WHERE id = ?", (run_id,))


def get_acceptance_rules_for_test(uc_id: int, test_id: str | None = None) -> list[dict]:
    """Returns active rules: all agent-wide rules + test-specific rules for this test_id."""
    with db() as conn:
        if test_id:
            rows = conn.execute(
                "SELECT * FROM acceptance_rules "
                "WHERE use_case_id = ? AND active = 1 "
                "  AND (scope = 'agent' OR (scope = 'test' AND source_test_id = ?)) "
                "ORDER BY id",
                (uc_id, test_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM acceptance_rules "
                "WHERE use_case_id = ? AND active = 1 AND scope = 'agent' "
                "ORDER BY id",
                (uc_id,),
            ).fetchall()
        return [dict(r) for r in rows]

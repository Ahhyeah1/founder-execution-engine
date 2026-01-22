import sqlite3
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any, Optional
import uuid

DB_PATH = Path(__file__).resolve().parent / "engine.db"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  goal_text TEXT NOT NULL,
  level INTEGER NOT NULL DEFAULT 1,
  xp INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0,
  debt INTEGER NOT NULL DEFAULT 0,
  difficulty INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  date TEXT NOT NULL,
  text TEXT NOT NULL,
  impact_weight REAL NOT NULL,
  difficulty INTEGER NOT NULL,
  non_negotiable INTEGER NOT NULL DEFAULT 1,
  completed INTEGER,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_user_date ON actions(user_id, date);

CREATE TABLE IF NOT EXISTS daily_results (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  date TEXT NOT NULL,
  xp_delta INTEGER NOT NULL,
  penalty INTEGER NOT NULL,
  verdict_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_user_date ON daily_results(user_id, date);
"""

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def today_str() -> str:
    return date.today().isoformat()

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(SCHEMA)

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def level_from_xp(xp: int) -> int:
    return clamp(1 + (xp // 250), 1, 10)

@dataclass
class Judgement:
    xp_delta: int
    penalty: int
    new_xp: int
    new_level: int
    new_streak: int
    new_debt: int
    new_difficulty: int
    verdict: str

def judge_day(
    *,
    current_xp: int,
    current_streak: int,
    current_debt: int,
    current_difficulty: int,
    completed: int,
    missed: int,
    impacts_sum: float
) -> Judgement:
    base_xp = int(round(20 * completed + 10 * impacts_sum + 5 * current_difficulty))
    penalty = 15 * missed

    if missed == 0 and completed > 0:
        new_streak = current_streak + 1
    else:
        new_streak = 0

    streak_bonus = 5 if new_streak >= 3 else 0

    xp_delta = base_xp + streak_bonus - penalty
    new_xp = max(0, current_xp + xp_delta)

    new_debt = current_debt + missed

    diff = current_difficulty
    if missed >= 2:
        diff += 1
    elif new_streak >= 5:
        diff += 1
    elif missed == 0 and completed >= 4:
        diff += 1

    new_difficulty = clamp(diff, 1, 5)
    new_level = level_from_xp(new_xp)

    if completed == 0 and missed > 0:
        verdict = "You executed nothing. That's self-deception. Penalty applied."
    elif missed == 0 and completed >= 4:
        verdict = "You executed hard. Keep going. Next level demands more."
    elif missed == 0 and completed > 0:
        verdict = "You did the work. No excuses. No detours."
    elif missed >= 2:
        verdict = "You avoided the main goal. You pay now and later. Fix it."
    else:
        verdict = "You did something — then you bailed on the rest. Not enough."

    return Judgement(
        xp_delta=xp_delta,
        penalty=penalty,
        new_xp=new_xp,
        new_level=new_level,
        new_streak=new_streak,
        new_debt=new_debt,
        new_difficulty=new_difficulty,
        verdict=verdict,
    )

def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

def upsert_user(user_id: str, goal_text: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT OR IGNORE INTO users(id, created_at, goal_text) VALUES(?,?,?)",
                    (user_id, now_iso(), goal_text.strip()))
        con.execute("UPDATE users SET goal_text=? WHERE id=?", (goal_text.strip(), user_id))

def list_actions(user_id: str, day: str) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM actions WHERE user_id=? AND date=? ORDER BY rowid",
            (user_id, day),
        ).fetchall()
        return [dict(r) for r in rows]

def insert_actions(user_id: str, day: str, actions: List[Dict[str, Any]]) -> None:
    with sqlite3.connect(DB_PATH) as con:
        for a in actions[:5]:
            con.execute(
                """INSERT INTO actions(id, user_id, date, text, impact_weight, difficulty, non_negotiable, completed, completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    user_id,
                    day,
                    str(a["text"]).strip()[:300],
                    float(a.get("impact_weight", 1.0)),
                    int(a.get("difficulty", 2)),
                    1,
                    None,
                    None,
                ),
            )

def set_action_completion(action_id: str, completed: bool) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE actions SET completed=?, completed_at=? WHERE id=?",
            (1 if completed else 0, now_iso(), action_id),
        )

def upsert_daily_result(user_id: str, day: str, j: Judgement) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        existing = con.execute(
            "SELECT id FROM daily_results WHERE user_id=? AND date=?",
            (user_id, day),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE daily_results SET xp_delta=?, penalty=?, verdict_text=?, created_at=? WHERE id=?",
                (j.xp_delta, j.penalty, j.verdict, now_iso(), existing["id"]),
            )
        else:
            con.execute(
                "INSERT INTO daily_results(id, user_id, date, xp_delta, penalty, verdict_text, created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, day, j.xp_delta, j.penalty, j.verdict, now_iso()),
            )

def update_user_stats(user_id: str, j: Judgement) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE users SET xp=?, level=?, streak=?, debt=?, difficulty=? WHERE id=?",
            (j.new_xp, j.new_level, j.new_streak, j.new_debt, j.new_difficulty, user_id),
        )

def history(user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT date, xp_delta, penalty, verdict_text, created_at FROM daily_results WHERE user_id=? ORDER BY date DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

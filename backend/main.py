from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, date
import uuid

from db import init_db, get_conn
from ai import generate_actions
from rules import judge_day

app = FastAPI(title="Founder Execution Engine")

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def today_str():
    return date.today().isoformat()

class CreateUserReq(BaseModel):
    user_id: str = Field(..., min_length=3, max_length=64)
    goal_text: str = Field(..., min_length=5, max_length=280)

class GenerateReq(BaseModel):
    user_id: str

class CheckInReq(BaseModel):
    user_id: str
    action_updates: list[dict]  # [{id, completed: bool}]

@app.on_event("startup")
def startup():
    init_db()

@app.post("/users")
def create_user(req: CreateUserReq):
    with get_conn() as con:
        existing = con.execute("SELECT id FROM users WHERE id=?", (req.user_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User already exists.")
        con.execute(
            "INSERT INTO users(id, created_at, goal_text, level, xp, streak, debt, difficulty) VALUES(?,?,?,?,?,?,?,?)",
            (req.user_id, now_iso(), req.goal_text.strip(), 1, 0, 0, 0, 1),
        )
    return {"ok": True, "user_id": req.user_id}

@app.get("/users/{user_id}")
def get_user(user_id: str):
    with get_conn() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="User not found.")
        return dict(u)

@app.post("/generate_today")
def generate_today(req: GenerateReq):
    d = today_str()
    with get_conn() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (req.user_id,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="User not found.")

        # If already generated for today, return existing
        existing = con.execute(
            "SELECT * FROM actions WHERE user_id=? AND date=? ORDER BY rowid",
            (req.user_id, d),
        ).fetchall()
        if existing:
            return {"date": d, "actions": [dict(r) for r in existing]}

        # brief history (last 7 days)
        hist_rows = con.execute(
            "SELECT date, xp_delta, penalty FROM daily_results WHERE user_id=? ORDER BY date DESC LIMIT 7",
            (req.user_id,),
        ).fetchall()
        history = "; ".join([f\"{r['date']}: xpÎ”={r['xp_delta']}, pen={r['penalty']}\" for r in hist_rows])

        actions = generate_actions(u["goal_text"], int(u["difficulty"]), history=history)

        for a in actions:
            aid = str(uuid.uuid4())
            con.execute(
                \"\"\"INSERT INTO actions(id, user_id, date, text, impact_weight, difficulty, non_negotiable, completed, completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)\"\"\",
                (aid, req.user_id, d, a["text"], float(a["impact_weight"]), int(a["difficulty"]), 1, None, None),
            )

        out = con.execute(
            "SELECT * FROM actions WHERE user_id=? AND date=? ORDER BY rowid",
            (req.user_id, d),
        ).fetchall()
        return {"date": d, "actions": [dict(r) for r in out]}

@app.post("/checkin")
def checkin(req: CheckInReq):
    d = today_str()
    with get_conn() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (req.user_id,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="User not found.")

        actions = con.execute(
            "SELECT * FROM actions WHERE user_id=? AND date=? ORDER BY rowid",
            (req.user_id, d),
        ).fetchall()
        if not actions:
            raise HTTPException(status_code=400, detail="No actions for today. Call /generate_today first.")

        # Apply updates
        action_map = {a["id"]: a for a in actions}
        for upd in req.action_updates:
            aid = upd.get("id")
            completed = upd.get("completed")
            if aid not in action_map:
                continue
            con.execute(
                "UPDATE actions SET completed=?, completed_at=? WHERE id=?",
                (1 if completed else 0, now_iso(), aid),
            )

        # Re-read after updates
        actions2 = con.execute(
            "SELECT * FROM actions WHERE user_id=? AND date=? ORDER BY rowid",
            (req.user_id, d),
        ).fetchall()

        completed = sum(1 for a in actions2 if a["completed"] == 1)
        missed = sum(1 for a in actions2 if a["completed"] == 0)
        impacts_sum = sum(float(a["impact_weight"]) for a in actions2 if a["completed"] == 1)

        j = judge_day(
            current_xp=int(u["xp"]),
            current_streak=int(u["streak"]),
            current_debt=int(u["debt"]),
            current_difficulty=int(u["difficulty"]),
            completed=completed,
            missed=missed,
            impacts_sum=impacts_sum
        )

        # Upsert daily result
        existing_res = con.execute(
            "SELECT id FROM daily_results WHERE user_id=? AND date=?",
            (req.user_id, d),
        ).fetchone()
        if existing_res:
            con.execute(
                "UPDATE daily_results SET xp_delta=?, penalty=?, verdict_text=?, created_at=? WHERE id=?",
                (j.xp_delta, j.penalty, j.verdict, now_iso(), existing_res["id"]),
            )
        else:
            rid = str(uuid.uuid4())
            con.execute(
                "INSERT INTO daily_results(id, user_id, date, xp_delta, penalty, verdict_text, created_at) VALUES(?,?,?,?,?,?,?)",
                (rid, req.user_id, d, j.xp_delta, j.penalty, j.verdict, now_iso()),
            )

        # Update user stats
        con.execute(
            "UPDATE users SET xp=?, level=?, streak=?, debt=?, difficulty=? WHERE id=?",
            (j.new_xp, j.new_level, j.new_streak, j.new_debt, j.new_difficulty, req.user_id),
        )

        return {
            "date": d,
            "completed": completed,
            "missed": missed,
            "xp_delta": j.xp_delta,
            "penalty": j.penalty,
            "xp": j.new_xp,
            "level": j.new_level,
            "streak": j.new_streak,
            "debt": j.new_debt,
            "difficulty": j.new_difficulty,
            "verdict": j.verdict,
            "actions": [dict(a) for a in actions2],
        }

@app.get("/history/{user_id}")
def history(user_id: str):
    with get_conn() as con:
        u = con.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="User not found.")
        results = con.execute(
            "SELECT date, xp_delta, penalty, verdict_text, created_at FROM daily_results WHERE user_id=? ORDER BY date DESC LIMIT 30",
            (user_id,),
        ).fetchall()
        return {"results": [dict(r) for r in results]}

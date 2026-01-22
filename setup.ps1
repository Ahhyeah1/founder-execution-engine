$ErrorActionPreference = "Stop"

function WriteFile($path, $content) {
  $dir = Split-Path $path
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  Set-Content -Path $path -Value $content -Encoding UTF8
}

$root = Get-Location

Write-Host "==> Creating project structure..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path ".\backend" | Out-Null
New-Item -ItemType Directory -Force -Path ".\frontend" | Out-Null

Write-Host "==> Writing requirements.txt / README.md..." -ForegroundColor Cyan
WriteFile ".\requirements.txt" @"
fastapi==0.115.0
uvicorn==0.30.6
pydantic==2.8.2
requests==2.32.3
streamlit==1.37.1
"@

WriteFile ".\README.md" @"
# Founder Execution Engine (MVP)

## Run (Windows)
### Backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\requirements.txt
uvicorn main:app --reload --port 8000

### Frontend
cd ..\frontend
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\requirements.txt
streamlit run app.py

## Optional AI
Set OPENAI_API_KEY to use OpenAI action generation.
If not set, offline fallback is used.
"@

Write-Host "==> Writing backend files..." -ForegroundColor Cyan
WriteFile ".\backend\db.py" @"
import sqlite3
from contextlib import contextmanager
from pathlib import Path

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
  completed_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_user_date ON actions(user_id, date);

CREATE TABLE IF NOT EXISTS daily_results (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  date TEXT NOT NULL,
  xp_delta INTEGER NOT NULL,
  penalty INTEGER NOT NULL,
  verdict_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_results_user_date ON daily_results(user_id, date);
"""

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(SCHEMA)

@contextmanager
def get_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()
"@

WriteFile ".\backend\rules.py" @"
from dataclasses import dataclass

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

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def level_from_xp(xp: int) -> int:
    # Level up every 250 XP (cap 10)
    return clamp(1 + (xp // 250), 1, 10)

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
    # Deterministic + brutal:
    # Completed earns XP scaled by impact + current difficulty.
    base_xp = int(round(20 * completed + 10 * impacts_sum + 5 * current_difficulty))
    penalty = 15 * missed

    # Streak
    if missed == 0 and completed > 0:
        new_streak = current_streak + 1
    else:
        new_streak = 0

    # Streak bonus after day 3
    streak_bonus = 5 if new_streak >= 3 else 0

    xp_delta = base_xp + streak_bonus - penalty
    new_xp = max(0, current_xp + xp_delta)

    # Permanent debt from misses
    new_debt = current_debt + missed

    # Difficulty: misses punish you; strong streak also raises the bar.
    diff = current_difficulty
    if missed >= 2:
        diff += 1
    elif new_streak >= 5:
        diff += 1
    elif missed == 0 and completed >= 4:
        diff += 1

    new_difficulty = clamp(diff, 1, 5)
    new_level = level_from_xp(new_xp)

    # Verdict (confrontational, concise)
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
"@

WriteFile ".\backend\ai.py" @"
import os
import json
import re
from typing import List, Dict, Any
import requests

def _offline_actions(goal_text: str, difficulty: int) -> List[Dict[str, Any]]:
    goal = goal_text.strip()
    lower = goal.lower()
    actions: List[Dict[str, Any]] = []

    def add(text: str, w: float, d: int):
        actions.append({
            "text": text,
            "impact_weight": float(w),
            "difficulty": int(d),
            "non_negotiable": True
        })

    # Heuristic categories
    if any(k in lower for k in ["mrr", "sales", "customers", "customer", "revenue", "sell", "pipeline"]):
        add("Contact 10 prospects (DM/email) with ONE offer. Log replies.", 1.4, min(3, difficulty+1))
        add("Improve the offer (headline + price + guarantee). Publish it.", 1.2, difficulty)
        add("Book 1 short sales call (15 min). No research-avoidance.", 1.5, min(3, difficulty+1))
        add("Ask for money: send 1 invoice/checkout link or request a deposit.", 1.5, 3)
    elif any(k in lower for k in ["product", "mvp", "app", "build", "launch", "ship"]):
        add("Set a deadline: ship 1 concrete feature today. No side quests.", 1.3, difficulty)
        add("Cut 1 feature you 'want' but don't need. Commit the change.", 1.2, difficulty)
        add("Post a public update (X/LinkedIn) showing what you shipped.", 1.1, difficulty)
        add("Get 3 people to test and give feedback. Collect responses.", 1.4, min(3, difficulty+1))
    else:
        add("Write today's 3 deliverables in 1 sentence each. No fluff.", 1.0, difficulty)
        add("Do the most uncomfortable task first. 45-minute timer. No distractions.", 1.4, min(3, difficulty+1))
        add("Remove 1 blocker by contacting a human (not Googling).", 1.3, min(3, difficulty+1))
        add("Ship something visible: post/commit/demo. Proof > intention.", 1.2, difficulty)

    actions = actions[:5]
    if len(actions) < 3:
        actions.append({
            "text": "Ship a result you can show publicly.",
            "impact_weight": 1.2,
            "difficulty": difficulty,
            "non_negotiable": True
        })
    return actions

def generate_actions(goal_text: str, difficulty: int, history: str = "") -> List[Dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _offline_actions(goal_text, difficulty)

    # Minimal HTTPS call (no SDK). If it fails, fallback.
    try:
        prompt = f'''
You are a ruthless operating manager. Generate 3-5 DAILY, NON-NEGOTIABLE actions for a founder.

Rules:
- No administrative tasks.
- At least 1 action must be uncomfortable (contacting people, publishing, committing, asking for money).
- Actions must directly drive the goal.
- Return ONLY a JSON array of objects:
  {{ "text": "...", "impact_weight": 0.5-1.5, "difficulty": 1-3, "non_negotiable": true }}

GOAL: {goal_text}
DIFFICULTY (1-5): {difficulty}
HISTORY (brief): {history}
'''
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4.1-mini", "input": prompt, "temperature": 0.4},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        txt = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        txt += c.get("text", "")

        m = re.search(r"\[.*\]", txt, flags=re.S)
        if not m:
            return _offline_actions(goal_text, difficulty)

        arr = json.loads(m.group(0))
        cleaned: List[Dict[str, Any]] = []
        for a in arr:
            cleaned.append({
                "text": str(a.get("text", "")).strip()[:300],
                "impact_weight": float(a.get("impact_weight", 1.0)),
                "difficulty": int(a.get("difficulty", 2)),
                "non_negotiable": True
            })

        cleaned = cleaned[:5]
        if len(cleaned) < 3:
            return _offline_actions(goal_text, difficulty)
        return cleaned
    except Exception:
        return _offline_actions(goal_text, difficulty)
"@

WriteFile ".\backend\main.py" @"
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
        history = "; ".join([f\"{r['date']}: xpΔ={r['xp_delta']}, pen={r['penalty']}\" for r in hist_rows])

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
"@

Write-Host "==> Writing frontend..." -ForegroundColor Cyan
WriteFile ".\frontend\app.py" @"
import streamlit as st
import requests
from datetime import date

API = st.secrets.get('API_URL', 'http://127.0.0.1:8000')

st.set_page_config(page_title='Founder Execution Engine', layout='centered')

st.markdown('''
<style>
body { background-color: #0b0f14; color: #e6e6e6; }
.block-container { padding-top: 2rem; max-width: 760px; }
.stButton>button { width: 100%; border-radius: 10px; padding: 0.75rem; }
small { opacity: 0.7; }
</style>
''', unsafe_allow_html=True)

st.title('Founder Execution Engine')
st.caption('Brutal execution. Permanent record. No excuses.')

if 'user_id' not in st.session_state:
    st.session_state.user_id = ''
if 'actions' not in st.session_state:
    st.session_state.actions = []
if 'date' not in st.session_state:
    st.session_state.date = str(date.today())
if 'verdict' not in st.session_state:
    st.session_state.verdict = None

tabs = st.tabs(['Init', 'Today', 'Verdict', 'Stats'])

with tabs[0]:
    st.subheader('Init')
    user_id = st.text_input('User ID (short name)', value=st.session_state.user_id or 'founder')
    goal = st.text_area('Main goal (max 280 chars)', height=120, placeholder='Example: Get 10 paying customers in 14 days.')
    if st.button('Commit'):
        r = requests.post(f'{API}/users', json={'user_id': user_id, 'goal_text': goal})
        if r.status_code == 409:
            st.warning('User already exists. Continuing.')
        elif not r.ok:
            st.error(r.text); st.stop()
        st.session_state.user_id = user_id
        st.success('Goal locked. The game starts now.')

with tabs[1]:
    st.subheader('Today')
    if not st.session_state.user_id:
        st.info('Go to Init and create a user + goal first.')
        st.stop()

    if st.button(\"Generate today's actions\"):
        r = requests.post(f'{API}/generate_today', json={'user_id': st.session_state.user_id})
        if not r.ok:
            st.error(r.text); st.stop()
        data = r.json()
        st.session_state.actions = data['actions']
        st.session_state.date = data['date']
        st.success('Actions generated. They are non-negotiable.')

    if st.session_state.actions:
        st.write(f\"**Date:** {st.session_state.date}\")
        st.markdown('### Non-negotiable actions')
        updates = []
        for a in st.session_state.actions:
            key = f\"act_{a['id']}\"
            default = False
            if a['completed'] is not None:
                default = (a['completed'] == 1)
            val = st.checkbox(a['text'], value=default, key=key)
            updates.append({'id': a['id'], 'completed': bool(val)})

        if st.button('Submit check-in (judgement)'):
            r = requests.post(f'{API}/checkin', json={'user_id': st.session_state.user_id, 'action_updates': updates})
            if not r.ok:
                st.error(r.text); st.stop()
            st.session_state.verdict = r.json()
            st.success('Judgement saved. Go to Verdict.')

with tabs[2]:
    st.subheader('Verdict')
    v = st.session_state.verdict
    if not v:
        st.info('Do a check-in in Today first.')
        st.stop()

    st.markdown(f\"## {v['verdict']}\")
    st.write(f\"**XP Δ:** {v['xp_delta']}  |  **Penalty:** {v['penalty']}\")
    st.write(f\"**XP:** {v['xp']}  |  **Level:** {v['level']}  |  **Streak:** {v['streak']}  |  **Debt:** {v['debt']}  |  **Difficulty:** {v['difficulty']}\")
    
with tabs[3]:
    st.subheader('Stats')
    if not st.session_state.user_id:
        st.info('Create a user first.')
        st.stop()

    u = requests.get(f'{API}/users/{st.session_state.user_id}')
    if u.ok:
        u = u.json()
        st.write(f\"**Goal:** {u['goal_text']}\")
        st.write(f\"**XP:** {u['xp']}  |  **Level:** {u['level']}  |  **Streak:** {u['streak']}  |  **Debt:** {u['debt']}  |  **Difficulty:** {u['difficulty']}\")
    else:
        st.error(u.text)

    h = requests.get(f'{API}/history/{st.session_state.user_id}')
    if h.ok:
        rows = h.json()['results']
        if rows:
            st.markdown('### Permanent record (last 30 days)')
            for r in rows:
                st.markdown(
                    f\"**{r['date']}** — XPΔ {r['xp_delta']} | Pen {r['penalty']}  \\n{r['verdict_text']}  \\n<small>{r['created_at']}</small>\",
                    unsafe_allow_html=True
                )
                st.divider()
        else:
            st.caption('No judgements yet.')
    else:
        st.error(h.text)
"@

Write-Host "==> Creating venvs + installing deps..." -ForegroundColor Cyan

# Backend venv
Push-Location ".\backend"
python -m venv .venv
& .\.venv\Scripts\python -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\pip install -r ..\requirements.txt
Pop-Location

# Frontend venv
Push-Location ".\frontend"
python -m venv .venv
& .\.venv\Scripts\python -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\pip install -r ..\requirements.txt
Pop-Location

Write-Host "==> Starting backend + frontend..." -ForegroundColor Green
Write-Host "Backend: http://127.0.0.1:8000  | Frontend will open in your browser" -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$root\backend`"; .\.venv\Scripts\activate; uvicorn main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$root\frontend`"; .\.venv\Scripts\activate; streamlit run app.py"

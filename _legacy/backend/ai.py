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

import streamlit as st
from engine import (
    init_db, today_str, get_user, upsert_user,
    list_actions, insert_actions, set_action_completion,
    judge_day, upsert_daily_result, update_user_stats, history
)
from ai_actions import generate_actions

st.set_page_config(page_title="Founder Execution Engine", layout="centered")

st.markdown(
    \"""
<style>
body { background-color: #0b0f14; color: #e6e6e6; }
.block-container { padding-top: 2rem; max-width: 820px; }
.stButton>button { width: 100%; border-radius: 10px; padding: 0.75rem; }
small { opacity: 0.7; }
</style>
\""",
    unsafe_allow_html=True
)

st.title("Founder Execution Engine")
st.caption("Brutal execution. Permanent record. No excuses.")

init_db()

if "user_id" not in st.session_state:
    st.session_state.user_id = "founder"
if "today_actions" not in st.session_state:
    st.session_state.today_actions = []
if "today" not in st.session_state:
    st.session_state.today = today_str()
if "last_verdict" not in st.session_state:
    st.session_state.last_verdict = None

tabs = st.tabs(["Init", "Today", "Verdict", "Stats"])

with tabs[0]:
    st.subheader("Init")
    user_id = st.text_input("User ID (short name)", value=st.session_state.user_id)
    goal = st.text_area(
        "Main goal (max 280 chars)",
        height=120,
        placeholder="Example: Get 10 paying customers in 14 days."
    )

    if st.button("Commit goal"):
        if not goal.strip():
            st.error("Write a goal. No blanks.")
        else:
            upsert_user(user_id.strip(), goal.strip())
            st.session_state.user_id = user_id.strip()
            st.success("Goal locked. The game starts now.")

with tabs[1]:
    st.subheader("Today")
    uid = st.session_state.user_id
    u = get_user(uid)

    if not u:
        st.info("Go to Init and commit a goal first.")
        st.stop()

    st.write(f"**Goal:** {u['goal_text']}")
    st.write(f"**Date:** {st.session_state.today}")

    existing = list_actions(uid, st.session_state.today)
    if existing and not st.session_state.today_actions:
        st.session_state.today_actions = existing

    if st.button("Generate today's actions"):
        existing2 = list_actions(uid, st.session_state.today)
        if existing2:
            st.session_state.today_actions = existing2
            st.warning("Already generated for today. You don't get a re-roll.")
        else:
            hist = history(uid, limit=7)
            hist_str = "; ".join([f"{r['date']}: xpΔ={r['xp_delta']}, pen={r['penalty']}" for r in hist])
            actions = generate_actions(u["goal_text"], int(u["difficulty"]), history=hist_str)
            insert_actions(uid, st.session_state.today, actions)
            st.session_state.today_actions = list_actions(uid, st.session_state.today)
            st.success("Actions generated. They are non-negotiable.")

    if st.session_state.today_actions:
        st.markdown("### Non-negotiable actions")
        updates = []
        for a in st.session_state.today_actions:
            key = f"act_{a['id']}"
            default = False
            if a["completed"] is not None:
                default = (a["completed"] == 1)
            val = st.checkbox(a["text"], value=default, key=key)
            updates.append((a["id"], bool(val)))

        if st.button("Submit check-in (judgement)"):
            for action_id, completed in updates:
                set_action_completion(action_id, completed)

            actions2 = list_actions(uid, st.session_state.today)
            completed = sum(1 for a in actions2 if a["completed"] == 1)
            missed = sum(1 for a in actions2 if a["completed"] == 0)
            impacts_sum = sum(float(a["impact_weight"]) for a in actions2 if a["completed"] == 1)

            u2 = get_user(uid)
            j = judge_day(
                current_xp=int(u2["xp"]),
                current_streak=int(u2["streak"]),
                current_debt=int(u2["debt"]),
                current_difficulty=int(u2["difficulty"]),
                completed=completed,
                missed=missed,
                impacts_sum=impacts_sum,
            )
            upsert_daily_result(uid, st.session_state.today, j)
            update_user_stats(uid, j)

            st.session_state.last_verdict = {
                "completed": completed,
                "missed": missed,
                "xp_delta": j.xp_delta,
                "penalty": j.penalty,
                "verdict": j.verdict,
                "xp": j.new_xp,
                "level": j.new_level,
                "streak": j.new_streak,
                "debt": j.new_debt,
                "difficulty": j.new_difficulty,
            }
            st.success("Judgement saved. Go to Verdict.")

with tabs[2]:
    st.subheader("Verdict")
    v = st.session_state.last_verdict
    if not v:
        st.info("Submit a check-in first.")
        st.stop()

    st.markdown(f"## {v['verdict']}")
    st.write(f"**Completed:** {v['completed']} | **Missed:** {v['missed']}")
    st.write(f"**XP Δ:** {v['xp_delta']} | **Penalty:** {v['penalty']}")
    st.write(
        f"**XP:** {v['xp']} | **Level:** {v['level']} | **Streak:** {v['streak']} | **Debt:** {v['debt']} | **Difficulty:** {v['difficulty']}"
    )

with tabs[3]:
    st.subheader("Stats")
    uid = st.session_state.user_id
    u = get_user(uid)
    if not u:
        st.info("Commit a goal first.")
        st.stop()

    st.write(f"**Goal:** {u['goal_text']}")
    st.write(f"**XP:** {u['xp']} | **Level:** {u['level']} | **Streak:** {u['streak']} | **Debt:** {u['debt']} | **Difficulty:** {u['difficulty']}")

    rows = history(uid, limit=30)
    if rows:
        st.markdown("### Permanent record (last 30 days)")
        for r in rows:
            st.markdown(
                f"**{r['date']}** — XPΔ {r['xp_delta']} | Pen {r['penalty']}  \n{r['verdict_text']}  \n<small>{r['created_at']}</small>",
                unsafe_allow_html=True
            )
            st.divider()
    else:
        st.caption("No judgements yet.")

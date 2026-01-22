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

    if st.button("Generate today's actions"):
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
    st.write(f\"**XP Î”:** {v['xp_delta']}  |  **Penalty:** {v['penalty']}\")
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
                    f\"**{r['date']}** â€” XPÎ” {r['xp_delta']} | Pen {r['penalty']}  \\n{r['verdict_text']}  \\n<small>{r['created_at']}</small>\",
                    unsafe_allow_html=True
                )
                st.divider()
        else:
            st.caption('No judgements yet.')
    else:
        st.error(h.text)

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

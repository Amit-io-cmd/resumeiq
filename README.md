# ResumeIQ: explainable AI resume analyzer and job matcher

## Run
```
pip install -r requirements.txt
cd backend && uvicorn main:app --reload
```
Open http://127.0.0.1:8000. Click "Try a sample resume" for an instant demo.
Optional: `export ANTHROPIC_API_KEY=...` enables AI bullet rewrites.

## How matching works
score = 40% semantic similarity (MiniLM embeddings, TF-IDF fallback) + 40% skill overlap (taxonomy in data/skills.txt) + 20% experience fit.
The UI highlights the exact resume text that supports each matched skill.

## Structure
- backend/engine.py: parsing, skill extraction, hybrid matcher, ATS score, blind screening
- backend/db.py + schema.sql: SQLite tables and SQL analytics
- backend/main.py: FastAPI endpoints (/api/analyze, /api/recruiter, /api/rewrite, /api/insights)
- frontend/: HTML, CSS, JavaScript dashboard
- data/: skills taxonomy, 8 seed jobs, sample resume

## Pitch ideas
Show the highlighted evidence view, the recruiter blind-screening toggle, and the SQL market insights. Add more jobs in data/jobs.json (delete data/app.db to reseed) and extend skills.txt.

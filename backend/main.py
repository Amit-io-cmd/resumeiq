from typing import List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import engine as E, db

app = FastAPI(title="ResumeIQ")
db.init()

async def read(file: UploadFile) -> str:
    text = E.extract_text(file.filename or "resume.txt", await file.read()).strip()
    if len(text) < 80:
        raise HTTPException(400, "Could not read text from this file. Upload a text-based PDF, DOCX or TXT.")
    return text

def score_job(text, skills, years, job):
    m = E.match(text, skills, years, job["title"] + ". " + job["description"], job["skills"], job.get("min_years", 0))
    return {"job_id": job["id"], "title": job["title"], "company": job["company"], "location": job["location"], **m}

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), jd: str = Form("")):
    text = await read(file)
    skills, spans = E.find_skills(text)
    years = E.years_of_experience(text)
    ats_score, tips, *rest = E.ats(text)
    categories = rest[0] if rest else {}
    matches = sorted((score_job(text, skills, years, j) for j in db.jobs()), key=lambda m: -m["score"])
    custom = None
    if jd.strip():
        jskills, _ = E.find_skills(jd)
        custom = {"job_id": 0, "title": "Your pasted job description", "company": "Custom", "location": "",
                  **E.match(text, skills, years, jd, jskills)}
    rid = db.save_resume(file.filename, text, ats_score, years, skills, matches)
    return {"resume_id": rid, "text": text, "spans": spans, "skills": sorted(skills), "years": years,
            "ats": {"score": ats_score, "tips": tips, "categories": categories}, "matches": matches[:8], "custom": custom}

@app.post("/api/recruiter")
async def recruiter(files: List[UploadFile] = File(...), jd: str = Form(...), anonymize: bool = Form(False)):
    jskills, _ = E.find_skills(jd)
    if not jskills:
        raise HTTPException(400, "No known skills found in the job description. Paste a fuller description.")
    rows = []
    for i, f in enumerate(files):
        text = await read(f)
        skills, _ = E.find_skills(text)
        years = E.years_of_experience(text)
        m = E.match(E.anonymize(text) if anonymize else text, skills, years, jd, jskills)
        rows.append({"name": f"Candidate {chr(65 + i % 26)}{i // 26 or ''}" if anonymize else f.filename,
                     "years": years, "ats": E.ats(text)[0], **m})
    rows.sort(key=lambda r: -r["score"])
    return {"required": sorted(jskills), "candidates": rows}

@app.post("/api/rewrite")
async def rewrite(file: UploadFile = File(...)):
    text = await read(file)
    try:
        return {"suggestions": E.rewrite_bullets(text)}
    except Exception:
        raise HTTPException(503, "AI rewrite needs the 'anthropic' package and an ANTHROPIC_API_KEY environment variable.")

@app.get("/api/jobs")
def jobs(): return db.jobs()

@app.get("/api/insights")
def insights(): return db.insights()

@app.get("/api/sample")
def sample(): return JSONResponse({"text": (db.ROOT / "data" / "sample_resume.txt").read_text(encoding="utf-8")})

app.mount("/", StaticFiles(directory=db.ROOT / "frontend", html=True), name="web")

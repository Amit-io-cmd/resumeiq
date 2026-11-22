from typing import List
import logging
import json
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import engine as E, db

app = FastAPI(title="ResumeIQ")
db.init()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

async def read(file: UploadFile) -> str:
    filename = file.filename or ""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Each resume must be 10 MB or smaller.")
    try:
        text = E.extract_text(filename, data).strip()
    except E.ResumeFileError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(text) < 80:
        if filename.lower().endswith(".pdf"):
            raise HTTPException(400, "This PDF has little or no selectable text. If it is scanned, run OCR and upload a searchable PDF.")
        raise HTTPException(400, "Could not read text from this file. Upload a text-based PDF, DOCX or TXT.")
    return text

def score_job(text, skills, years, job):
    m = E.match(text, skills, years, job["title"] + ". " + job["description"], job["skills"], job.get("min_years", 0))
    return {"job_id": job["id"], "title": job["title"], "company": job["company"], "location": job["location"],
            "description": job["description"], **m}

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), jd: str = Form(""), top_n: int = Form(5)):
    text = await read(file)
    skills, spans = E.find_skills(text)
    years = E.years_of_experience(text)
    ats_score, tips, *rest = E.ats(text)
    categories = rest[0] if rest else {}
    matches = sorted((score_job(text, skills, years, j) for j in db.jobs()), key=lambda m: -m["score"])
    custom = None
    if jd.strip():
        jskills, _ = E.find_skills(jd)
        role = None
        if not jskills:
            role = E.resolve_role_profile(jd, db.jobs())
            if not role:
                raise HTTPException(400, "No skills or supported role were recognized. Try a role such as 'backend developer' or paste a fuller job description.")
            jskills = set(role["skills"])
        target_title = role["title"] if role else "Your pasted job description"
        target_company = "ResumeIQ role profile" if role else "Custom"
        target_text = f'{role["title"]}. {role["description"]}' if role else jd
        min_years = E.years_of_experience(jd) or (role.get("min_years", 0) if role else 0)
        custom = {"job_id": 0, "title": target_title, "company": target_company, "location": "", "description": target_text,
                  "keywords": E.compare_job_keywords(target_text, text),
                  **E.match(text, skills, years, target_text, jskills, min_years)}
    
    target_job = custom or (matches[0] if matches else None)
    missing_skills = target_job.get("missing", []) if target_job else []
    study_links = E.get_study_links_for_skills(missing_skills[:8])
    predicted_roles = E.predict_top_roles(text, skills, years, db.jobs(), top_n=top_n)

    rid = db.save_resume(file.filename, text, ats_score, years, skills, matches)
    return {"resume_id": rid, "text": text, "spans": spans, "skills": sorted(skills), "years": years,
            "ats": {"score": ats_score, "tips": tips, "categories": categories}, "matches": matches[:8], "custom": custom,
            "study_links": study_links, "predicted_roles": predicted_roles}

@app.post("/api/recruiter")
async def recruiter(files: List[UploadFile] = File(...), jd: str = Form(...), anonymize: bool = Form(False),
                    semantic_weight: float = Form(40, ge=0, le=100),
                    skill_weight: float = Form(40, ge=0, le=100),
                    experience_weight: float = Form(20, ge=0, le=100)):
    jskills, _ = E.find_skills(jd)
    if not jskills:
        raise HTTPException(400, "No known skills found in the job description. Paste a fuller description.")
    weights = {"semantic": semantic_weight, "skills": skill_weight, "experience": experience_weight}
    if sum(weights.values()) <= 0:
        raise HTTPException(400, "At least one score weight must be greater than zero.")
    required_years = E.years_of_experience(jd)
    if not any((semantic_weight > 0, skill_weight > 0, required_years > 0 and experience_weight > 0)):
        raise HTTPException(400, "The job description has no experience requirement. Give meaning similarity or skill overlap a positive weight.")
    rows = []
    for i, f in enumerate(files):
        text = await read(f)
        skills, _ = E.find_skills(text)
        years = E.years_of_experience(text)
        display_text = E.anonymize(text) if anonymize else text
        m = E.match(display_text, skills, years, jd, jskills, required_years, weights)
        rows.append({"name": f"Candidate {chr(65 + i % 26)}{i // 26 or ''}" if anonymize else f.filename,
                     "years": years, "ats": E.ats(text)[0], "evidence": E.skill_evidence(display_text, m["matched"]), **m})
    rows.sort(key=lambda r: -r["score"])
    return {"required": sorted(jskills), "candidates": rows}

@app.post("/api/rewrite")
async def rewrite(file: UploadFile = File(...)):
    text = await read(file)
    return {"suggestions": E.rewrite_suggestions(text)}


@app.post("/api/export/docx")
async def export_docx(file: UploadFile = File(...), edits: str = Form("[]")):
    text = await read(file)
    try:
        changes = json.loads(edits)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Resume edits must be valid JSON.") from exc
    if not isinstance(changes, list) or len(changes) > 12:
        raise HTTPException(400, "Provide up to 12 edited bullet suggestions.")
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("original"), str) or not isinstance(change.get("suggested"), str):
            raise HTTPException(400, "Each edit must include original and suggested text.")
        if change["original"] not in text or not change["suggested"].strip():
            raise HTTPException(400, "An edited bullet is empty or does not belong to this resume.")
    output = E.create_updated_docx(text, changes)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="resumeiq_updated.docx"'},
    )


@app.post("/api/outreach")
async def outreach(resume_text: str = Form(...), job_title: str = Form(...), company: str = Form(""),
                   job_description: str = Form(...)):
    if len(resume_text) < 80:
        raise HTTPException(400, "Analyze a readable resume before generating outreach.")
    return E.generate_outreach(resume_text, job_title, company, job_description)


@app.post("/api/roles/predict")
async def predict_roles(resume_text: str = Form(...), top_n: int = Form(5)):
    if not resume_text.strip():
        raise HTTPException(400, "Resume text is required.")
    skills, _ = E.find_skills(resume_text)
    years = E.years_of_experience(resume_text)
    n = max(1, min(20, top_n))
    return {"predicted_roles": E.predict_top_roles(resume_text, skills, years, db.jobs(), top_n=n)}

@app.post("/api/roles/open")
async def open_roles(role_query: str = Form(...), resume_text: str = Form("")):
    skills, _ = E.find_skills(resume_text) if resume_text else (set(), [])
    years = E.years_of_experience(resume_text) if resume_text else 0.0
    roles = E.fetch_matching_open_roles(role_query, resume_text, skills, years, limit=10)
    return {"open_roles": roles}

@app.post("/api/skills/study")
async def study_links(skills: str = Form(...)):
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    if not skill_list:
        return {"study_links": []}
    return {"study_links": E.get_study_links_for_skills(skill_list[:12])}

@app.get("/api/history")
def resume_history(filename: str):
    return {"versions": db.resume_history(filename)}

@app.get("/api/jobs")
def jobs(): return db.jobs()

@app.get("/api/insights")
def insights(): return db.insights()

@app.get("/api/sample")
def sample(): return JSONResponse({"text": (db.ROOT / "data" / "sample_resume.txt").read_text(encoding="utf-8")})

app.mount("/", StaticFiles(directory=db.ROOT / "frontend", html=True), name="web")

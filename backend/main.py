from __future__ import annotations
from typing import List, Optional, Dict, Any, Union
import logging
import json
import os
import sys
import pathlib
import time
import hmac
import hashlib
import urllib.request
import urllib.parse

# Ensure backend directory is in sys.path
BACKEND_DIR = pathlib.Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load .env file safely without external dependencies
for env_candidate in [
    pathlib.Path(__file__).resolve().parent.parent / ".env",
    pathlib.Path(__file__).resolve().parent / ".env"
]:
    if env_candidate.exists():
        try:
            for env_line in env_candidate.read_text(encoding="utf-8").splitlines():
                env_line = env_line.strip()
                if env_line and not env_line.startswith("#") and "=" in env_line:
                    k, v = env_line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import engine as E, db

app = FastAPI(title="ResumeIQ")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
db.init()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SESSION_SECRET = os.getenv("SESSION_SECRET", "resumeiq-secure-session-key-dev-fallback")
SESSION_COOKIE_NAME = "resumeiq_session"


# ---------- Session & Auth Token Management ----------
def sign_session_token(user_id: int) -> str:
    timestamp = int(time.time())
    payload = f"{user_id}:{timestamp}"
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> Optional[int]:
    if not token or token.count(":") != 2:
        return None
    try:
        user_id_str, timestamp_str, sig = token.split(":")
        payload = f"{user_id_str}:{timestamp_str}"
        expected_sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        # Max age: 30 days
        timestamp = int(timestamp_str)
        if time.time() - timestamp > (30 * 86400):
            return None
        return int(user_id_str)
    except Exception:
        return None


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    user_id = verify_session_token(token)
    if not user_id:
        return None
    return db.get_user_by_id(user_id)


# ---------- File Extraction & Validation ----------
async def read(file: UploadFile):
    filename = file.filename or ""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Each resume must be 10 MB or smaller.")
    try:
        text = E.extract_text(filename, data).strip()
    except E.ResumeFileError as exc:
        raise HTTPException(400, detail={"valid_resume": False, "message": str(exc), "warnings": []}) from exc

    # Structural & readability validity guard
    is_valid, err_msg, warnings, _ = E.validate_resume_text(text)
    if not is_valid:
        raise HTTPException(400, detail={"valid_resume": False, "message": err_msg, "warnings": warnings})

    return text, warnings


@app.post("/api/validate")
async def validate_upload(file: UploadFile = File(...)):
    filename = file.filename or ""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return {"valid_resume": False, "message": "File exceeds 10 MB limit. Please upload a smaller file.", "warnings": []}
    try:
        text = E.extract_text(filename, data).strip()
    except E.ResumeFileError as exc:
        return {"valid_resume": False, "message": str(exc), "warnings": []}

    is_valid, err_msg, warnings, details = E.validate_resume_text(text)
    return {
        "valid_resume": is_valid,
        "message": err_msg or "Valid resume format.",
        "warnings": warnings,
        "details": details
    }


def score_job(text, skills, years, job):
    m = E.match(text, skills, years, job["title"] + ". " + job["description"], job["skills"], job.get("min_years", 0))
    return {"job_id": job["id"], "title": job["title"], "company": job["company"], "location": job["location"],
            "description": job["description"], **m}


# ---------- Auth Endpoints ----------
@app.get("/auth/config")
def auth_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    return {
        "google_client_id": client_id,
        "dev_mode": not bool(client_id)
    }


@app.post("/auth/google")
async def auth_google(request: Request, response: Response, credential: str = Form(""), id_token: str = Form("")):
    token = credential.strip() or id_token.strip()
    if not token:
        raise HTTPException(400, "Google ID token or credential is required.")

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")

    # Local development bypass mode when client_id is not configured
    if not client_id or token.startswith("dev_"):
        user = db.upsert_google_user(
            google_sub="dev_local_user",
            email="demo-engineer@resumeiq.local",
            name="Dev Engineer",
            avatar_url=""
        )
        session_token = sign_session_token(user["id"])
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
            path="/"
        )
        return {"success": True, "user": user, "dev_mode": True}

    # Verify ID token with Google's tokeninfo endpoint
    try:
        req_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote_plus(token)}"
        req = urllib.request.Request(req_url, headers={"User-Agent": "ResumeIQ-Auth/1.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status != 200:
                raise HTTPException(401, "Google token verification failed.")
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Google token verification network call error: %s", exc)
        raise HTTPException(401, "Could not verify Google ID token with Google servers.")

    # Validate audience
    if payload.get("aud") != client_id:
        raise HTTPException(401, "Google ID token audience mismatch.")

    google_sub = payload.get("sub")
    email = payload.get("email", "")
    name = payload.get("name") or email.split("@")[0] or "User"
    avatar_url = payload.get("picture", "")

    if not google_sub or not email:
        raise HTTPException(400, "Incomplete profile information in Google ID token.")

    user = db.upsert_google_user(google_sub, email, name, avatar_url)
    session_token = sign_session_token(user["id"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/"
    )
    return {"success": True, "user": user, "dev_mode": False}


@app.post("/auth/dev-login")
def dev_login(response: Response):
    user = db.upsert_google_user(
        google_sub="dev_local_demo_account",
        email="demo-engineer@resumeiq.local",
        name="Demo Engineer (Local Dev)",
        avatar_url=""
    )
    session_token = sign_session_token(user["id"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/"
    )
    return {"success": True, "user": user, "dev_mode": True}


@app.post("/auth/custom-login")
def custom_login(response: Response, name: str = Form(""), email: str = Form("")):
    clean_name = name.strip() or "Engineer"
    clean_email = email.strip() or "engineer@resumeiq.local"
    google_sub = f"account_{hashlib.sha256(clean_email.encode('utf-8')).hexdigest()[:16]}"
    user = db.upsert_google_user(
        google_sub=google_sub,
        email=clean_email,
        name=clean_name,
        avatar_url=""
    )
    session_token = sign_session_token(user["id"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/"
    )
    return {"success": True, "user": user, "dev_mode": True}


@app.get("/auth/me")
def auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"user": None, "history": []}
    history = db.get_user_resumes(user["id"], limit=10)
    return {"user": user, "history": history}


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"success": True}


# ---------- Resume Analysis & Matching ----------
@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile = File(...), jd: str = Form(""), top_n: int = Form(5)):
    text, warnings = await read(file)
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
    portfolio_projects = E.get_portfolio_project_roadmaps(missing_skills[:6])
    official_docs = E.get_official_docs_and_certs(missing_skills[:6])
    salary_estimate = E.estimate_salary_range(target_job["title"] if target_job else "Software Engineer", years, skills)

    user = get_current_user(request)
    user_id = user["id"] if user else None
    rid = db.save_resume(file.filename, text, ats_score, years, skills, matches, user_id=user_id)

    return {"resume_id": rid, "text": text, "spans": spans, "skills": sorted(skills), "years": years,
            "ats": {"score": ats_score, "tips": tips, "categories": categories}, "matches": matches[:8], "custom": custom,
            "study_links": study_links, "predicted_roles": predicted_roles,
            "portfolio_projects": portfolio_projects, "official_docs": official_docs,
            "salary_estimate": salary_estimate,
            "valid_resume": True, "warnings": warnings}


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
        text, _ = await read(f)
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
    text, _ = await read(file)
    return {"suggestions": E.rewrite_suggestions(text)}


@app.post("/api/export/docx")
async def export_docx(file: UploadFile = File(...), edits: str = Form("[]")):
    text, _ = await read(file)
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
async def open_roles(role_query: str = Form("Software Engineer"), resume_text: str = Form("")):
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


# ---------- Career Pathing & Up-skilling Roadmaps ----------
@app.post("/api/skills/roadmap")
def skill_roadmap(skills: str = Form(...)):
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    projects = E.get_portfolio_project_roadmaps(skill_list[:8])
    docs = E.get_official_docs_and_certs(skill_list[:8])
    return {"portfolio_projects": projects, "official_docs": docs}


@app.get("/api/skills/track")
def get_tracked_skills(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    return {"tracked_skills": db.get_skill_trackers(user_id)}


@app.post("/api/skills/track")
def save_tracked_skill(request: Request, skill: str = Form(...), status: str = Form("to_learn"), notes: str = Form("")):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    item = db.upsert_skill_tracker(user_id, skill, status, notes)
    return {"success": True, "item": item}


@app.delete("/api/skills/track")
def delete_tracked_skill(request: Request, skill: str = Form(...)):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    db.delete_skill_tracker(user_id, skill)
    return {"success": True}


# ---------- Compensation & Salary Estimator ----------
@app.post("/api/salary/estimate")
def estimate_salary(role_title: str = Form("Software Engineer"), years: float = Form(3.0), skills: str = Form("")):
    skill_set = {s.strip() for s in skills.split(",") if s.strip()}
    return E.estimate_salary_range(role_title, years, skill_set)


# ---------- Job Application Tracking (Kanban CRM) ----------
@app.get("/api/crm/jobs")
def get_crm_jobs(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    jobs = db.get_job_applications(user_id)
    stages = {"saved": [], "applied": [], "interviewing": [], "offer": []}
    for j in jobs:
        st = j.get("stage", "saved")
        if st in stages:
            stages[st].append(j)
        else:
            stages["saved"].append(j)
    return {"jobs": jobs, "stages": stages}


@app.post("/api/crm/jobs")
def create_crm_job(request: Request, role_title: str = Form(...), company: str = Form(...),
                   location: str = Form("Remote"), salary_range: str = Form("$95k - $135k"),
                   match_score: float = Form(0), stage: str = Form("saved"),
                   url: str = Form(""), notes: str = Form("")):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    job = db.add_job_application(user_id, role_title, company, location, salary_range, match_score, stage, url, notes)
    return {"success": True, "job": job}


@app.patch("/api/crm/jobs/{app_id}")
def update_crm_stage(app_id: int, request: Request, stage: str = Form(...), notes: Optional[str] = Form(None)):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    updated = db.update_job_application_stage(app_id, user_id, stage, notes)
    return {"success": True, "job": updated}


@app.delete("/api/crm/jobs/{app_id}")
def delete_crm_job(app_id: int, request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else 0
    db.delete_job_application(app_id, user_id)
    return {"success": True}


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

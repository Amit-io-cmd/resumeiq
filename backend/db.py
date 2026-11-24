from __future__ import annotations
import sqlite3, json, pathlib, time
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
import engine
ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "app.db"

def conn():
    c = sqlite3.connect(DB, timeout=30.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 30000")
    return c

def skill_id(c, name):
    c.execute("INSERT OR IGNORE INTO skills(name) VALUES (?)", (name,))
    return c.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()[0]

def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript((pathlib.Path(__file__).parent / "schema.sql").read_text())
        cols = [col[1] for col in c.execute("PRAGMA table_info(resumes)").fetchall()]
        if "user_id" not in cols:
            c.execute("ALTER TABLE resumes ADD COLUMN user_id INTEGER REFERENCES users(id)")
        for job in json.loads((ROOT / "data" / "jobs.json").read_text()):
            existing = c.execute(
                "SELECT id FROM jobs WHERE title=? AND company=?",
                (job["title"], job["company"]),
            ).fetchone()
            if existing:
                job_id = existing["id"]
            else:
                cursor = c.execute(
                    "INSERT INTO jobs(title,company,location,description,min_years) VALUES (?,?,?,?,?)",
                    (job["title"], job["company"], job["location"], job["description"], job.get("min_years", 0)),
                )
                job_id = cursor.lastrowid
            stored_job = c.execute(
                "SELECT title, description FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            found, _ = engine.find_skills(stored_job["title"] + " " + stored_job["description"])
            c.execute("DELETE FROM job_skills WHERE job_id=?", (job_id,))
            for name in found:
                c.execute("INSERT OR IGNORE INTO job_skills VALUES (?,?)", (job_id, skill_id(c, name)))

def jobs():
    with conn() as c:
        out = []
        for j in c.execute("SELECT * FROM jobs"):
            sk = [r[0] for r in c.execute("SELECT s.name FROM job_skills js JOIN skills s ON s.id=js.skill_id WHERE js.job_id=?", (j["id"],))]
            out.append({**dict(j), "skills": sk})
        return out

def save_resume(filename, text, ats_score, years, skills, matches, user_id=None):
    with conn() as c:
        rid = c.execute(
            "INSERT INTO resumes(user_id, filename, raw_text, ats_score, years) VALUES (?,?,?,?,?)",
            (user_id, filename, text, ats_score, years)
        ).lastrowid
        for s in skills:
            c.execute("INSERT OR IGNORE INTO resume_skills VALUES (?,?)", (rid, skill_id(c, s)))
        for m in matches:
            c.execute("INSERT INTO matches(resume_id,job_id,score,missing_skills) VALUES (?,?,?,?)",
                      (rid, m["job_id"], m["score"], ",".join(m.get("missing", []))))
        return rid


def resume_history(filename):
    with conn() as c:
        rows = c.execute(
            "SELECT id, filename, ats_score, uploaded_at FROM resumes WHERE filename=? ORDER BY id",
            (filename,),
        ).fetchall()
        return [
            {"id": row["id"], "version": version, "filename": row["filename"],
             "ats_score": row["ats_score"], "uploaded_at": row["uploaded_at"]}
            for version, row in enumerate(rows, 1)
        ]


def insights():
    with conn() as c:
        q = lambda sql: [dict(r) for r in c.execute(sql)]
        return {
            "in_demand": q("SELECT s.name, COUNT(*) AS jobs FROM job_skills js JOIN skills s ON s.id=js.skill_id GROUP BY s.name ORDER BY jobs DESC LIMIT 8"),
            "avg_by_job": q("SELECT j.title, ROUND(AVG(m.score),1) AS avg_score, COUNT(*) AS n FROM matches m JOIN jobs j ON j.id=m.job_id GROUP BY j.id ORDER BY avg_score DESC LIMIT 8"),
            "resumes": c.execute("SELECT COUNT(*) FROM resumes").fetchone()[0],
        }


# ---------- User & Auth Management ----------
def upsert_google_user(google_sub: str, email: str, name: str, avatar_url: str):
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE google_sub=? OR email=?", (google_sub, email)).fetchone()
        if row:
            c.execute(
                "UPDATE users SET google_sub=?, email=?, name=?, avatar_url=?, last_login_at=CURRENT_TIMESTAMP WHERE id=?",
                (google_sub, email, name, avatar_url, row["id"])
            )
            user_id = row["id"]
        else:
            cursor = c.execute(
                "INSERT INTO users(google_sub, email, name, avatar_url) VALUES (?, ?, ?, ?)",
                (google_sub, email, name, avatar_url)
            )
            user_id = cursor.lastrowid
        user_row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(user_row)


def get_user_by_id(user_id: int):
    with conn() as c:
        row = c.execute(
            "SELECT id, google_sub, email, name, avatar_url, created_at, last_login_at FROM users WHERE id=?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str):
    with conn() as c:
        row = c.execute(
            "SELECT id, google_sub, email, name, avatar_url, created_at, last_login_at FROM users WHERE email=?",
            (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_resumes(user_id: int, limit: int = 10):
    with conn() as c:
        rows = c.execute(
            "SELECT id, filename, ats_score, years, uploaded_at FROM resumes WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Cache Helpers ----------
def get_cached_youtube(skill: str):
    with conn() as c:
        row = c.execute("SELECT title, channel, url FROM youtube_cache WHERE LOWER(skill)=LOWER(?)", (skill,)).fetchone()
        if row:
            return {"title": row["title"], "channel": row["channel"], "url": row["url"]}
        return None


def save_youtube_cache(skill: str, title: str, channel: str, url: str):
    with conn() as c:
        c.execute(
            "INSERT INTO youtube_cache(skill, title, channel, url, cached_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(skill) DO UPDATE SET title=excluded.title, channel=excluded.channel, url=excluded.url, cached_at=CURRENT_TIMESTAMP",
            (skill, title, channel, url)
        )


def get_cached_external_jobs(query: str, ttl_seconds: int = 3600):
    normalized = query.strip().lower()
    with conn() as c:
        row = c.execute("SELECT raw_json, cached_at FROM external_jobs_cache WHERE query=?", (normalized,)).fetchone()
        if not row:
            return None
        cached_at_str = row["cached_at"]
        try:
            cached_dt = datetime.strptime(cached_at_str, "%Y-%m-%d %H:%M:%S")
            age = (datetime.now() - cached_dt).total_seconds()
            if age > ttl_seconds:
                return None
        except Exception:
            pass
        try:
            return json.loads(row["raw_json"])
        except Exception:
            return None


def save_external_jobs_cache(query: str, jobs_data):
    normalized = query.strip().lower()
    raw = json.dumps(jobs_data)
    with conn() as c:
        c.execute(
            "INSERT INTO external_jobs_cache(query, raw_json, cached_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(query) DO UPDATE SET raw_json=excluded.raw_json, cached_at=CURRENT_TIMESTAMP",
            (normalized, raw)
        )


# ---------- Skill Gap Trackers (Upskilling Roadmap) ----------
def get_skill_trackers(user_id: int = 0):
    with conn() as c:
        rows = c.execute(
            "SELECT id, user_id, skill, status, notes, updated_at FROM skill_trackers WHERE user_id=? ORDER BY updated_at DESC",
            (user_id or 0,)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_skill_tracker(user_id: int = 0, skill: str = "", status: str = "to_learn", notes: str = ""):
    skill_clean = skill.strip()
    if not skill_clean:
        return None
    with conn() as c:
        c.execute(
            "INSERT INTO skill_trackers(user_id, skill, status, notes, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, skill) DO UPDATE SET status=excluded.status, notes=excluded.notes, updated_at=CURRENT_TIMESTAMP",
            (user_id or 0, skill_clean, status, notes)
        )
        row = c.execute("SELECT * FROM skill_trackers WHERE user_id=? AND skill=?", (user_id or 0, skill_clean)).fetchone()
        return dict(row) if row else None


def delete_skill_tracker(user_id: int = 0, skill: str = ""):
    with conn() as c:
        c.execute("DELETE FROM skill_trackers WHERE user_id=? AND skill=?", (user_id or 0, skill.strip()))
        return True


# ---------- Job Applications (Kanban CRM) ----------
def get_job_applications(user_id: int = 0):
    with conn() as c:
        rows = c.execute(
            "SELECT id, user_id, role_title, company, location, salary_range, match_score, stage, url, notes, created_at, updated_at "
            "FROM job_applications WHERE user_id=? ORDER BY id DESC",
            (user_id or 0,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_job_application(user_id: int = 0, role_title: str = "", company: str = "", location: str = "Remote",
                        salary_range: str = "$95k - $135k", match_score: float = 0, stage: str = "saved",
                        url: str = "", notes: str = ""):
    with conn() as c:
        cursor = c.execute(
            "INSERT INTO job_applications(user_id, role_title, company, location, salary_range, match_score, stage, url, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id or 0, role_title.strip(), company.strip(), location.strip(), salary_range.strip(),
             float(match_score), stage.strip().lower(), url.strip(), notes.strip())
        )
        app_id = cursor.lastrowid
        row = c.execute("SELECT * FROM job_applications WHERE id=?", (app_id,)).fetchone()
        return dict(row) if row else None


def update_job_application_stage(app_id: int, user_id: int = 0, stage: str = "saved", notes: Optional[str] = None):
    with conn() as c:
        if notes is not None:
            c.execute(
                "UPDATE job_applications SET stage=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND (user_id=? OR user_id=0)",
                (stage.strip().lower(), notes.strip(), app_id, user_id or 0)
            )
        else:
            c.execute(
                "UPDATE job_applications SET stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND (user_id=? OR user_id=0)",
                (stage.strip().lower(), app_id, user_id or 0)
            )
        row = c.execute("SELECT * FROM job_applications WHERE id=?", (app_id,)).fetchone()
        return dict(row) if row else None


def delete_job_application(app_id: int, user_id: int = 0):
    with conn() as c:
        c.execute("DELETE FROM job_applications WHERE id=? AND (user_id=? OR user_id=0)", (app_id, user_id or 0))
        return True

# Ensure all schema tables and columns exist on load
try:
    with conn() as _c:
        _c.executescript((pathlib.Path(__file__).parent / "schema.sql").read_text())
        cols = [col[1] for col in _c.execute("PRAGMA table_info(resumes)").fetchall()]
        if "user_id" not in cols:
            _c.execute("ALTER TABLE resumes ADD COLUMN user_id INTEGER REFERENCES users(id)")
except Exception:
    pass

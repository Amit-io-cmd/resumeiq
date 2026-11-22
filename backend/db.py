import sqlite3, json, pathlib, time
from datetime import datetime, timezone
import engine
ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "app.db"

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def skill_id(c, name):
    c.execute("INSERT OR IGNORE INTO skills(name) VALUES (?)", (name,))
    return c.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()[0]

def init():
    with conn() as c:
        c.executescript((pathlib.Path(__file__).parent / "schema.sql").read_text())
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

def save_resume(filename, text, ats_score, years, skills, matches):
    with conn() as c:
        rid = c.execute("INSERT INTO resumes(filename,raw_text,ats_score,years) VALUES (?,?,?,?)", (filename, text, ats_score, years)).lastrowid
        for s in skills:
            c.execute("INSERT OR IGNORE INTO resume_skills VALUES (?,?)", (rid, skill_id(c, s)))
        for m in matches:
            c.execute("INSERT INTO matches(resume_id,job_id,score,missing_skills) VALUES (?,?,?,?)",
                      (rid, m["job_id"], m["score"], ",".join(m["missing"])))
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
        # Parse timestamp
        cached_at_str = row["cached_at"]
        try:
            # Handle standard SQLite format 'YYYY-MM-DD HH:MM:SS'
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

# Ensure all schema tables exist on load
try:
    with conn() as _c:
        _c.executescript((pathlib.Path(__file__).parent / "schema.sql").read_text())
except Exception:
    pass


CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, title TEXT, company TEXT, location TEXT, description TEXT, min_years REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS job_skills (job_id INTEGER REFERENCES jobs(id), skill_id INTEGER REFERENCES skills(id), PRIMARY KEY (job_id, skill_id));
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, google_sub TEXT UNIQUE, email TEXT UNIQUE, name TEXT, avatar_url TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, last_login_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS resumes (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), filename TEXT, raw_text TEXT, ats_score REAL, years REAL, uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS resume_skills (resume_id INTEGER REFERENCES resumes(id), skill_id INTEGER REFERENCES skills(id), PRIMARY KEY (resume_id, skill_id));
CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, resume_id INTEGER REFERENCES resumes(id), job_id INTEGER REFERENCES jobs(id), score REAL, missing_skills TEXT);
CREATE TABLE IF NOT EXISTS youtube_cache (skill TEXT PRIMARY KEY, title TEXT, channel TEXT, url TEXT, cached_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS external_jobs_cache (query TEXT PRIMARY KEY, raw_json TEXT, cached_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS skill_trackers (
    id INTEGER PRIMARY KEY,
    user_id INTEGER DEFAULT 0,
    skill TEXT NOT NULL,
    status TEXT DEFAULT 'to_learn',
    notes TEXT DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, skill)
);
CREATE TABLE IF NOT EXISTS job_applications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER DEFAULT 0,
    role_title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT 'Remote',
    salary_range TEXT DEFAULT '$95k - $135k',
    match_score REAL DEFAULT 0,
    stage TEXT DEFAULT 'saved',
    url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

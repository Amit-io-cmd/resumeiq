CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, title TEXT, company TEXT, location TEXT, description TEXT, min_years REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS job_skills (job_id INTEGER REFERENCES jobs(id), skill_id INTEGER REFERENCES skills(id), PRIMARY KEY (job_id, skill_id));
CREATE TABLE IF NOT EXISTS resumes (id INTEGER PRIMARY KEY, filename TEXT, raw_text TEXT, ats_score REAL, years REAL, uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS resume_skills (resume_id INTEGER REFERENCES resumes(id), skill_id INTEGER REFERENCES skills(id), PRIMARY KEY (resume_id, skill_id));
CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY, resume_id INTEGER REFERENCES resumes(id), job_id INTEGER REFERENCES jobs(id), score REAL, missing_skills TEXT);

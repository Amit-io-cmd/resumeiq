"""NLP + ML engine: parsing, skill extraction, hybrid matching, ATS scoring, and interview prep."""
import io, re, os, pathlib, logging, datetime
from typing import List, Dict, Tuple, Set, Any

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
W_SEM, W_SKILL, W_EXP = 0.40, 0.40, 0.20   # hybrid score weights
logger = logging.getLogger(__name__)

# ---------- skills taxonomy (data/skills.txt: canonical|alias|alias) ----------
SKILLS = {}
SKILL_CATEGORY = {}

DEFAULT_CATEGORIES = {
    'Languages': {'Python', 'JavaScript', 'TypeScript', 'Java', 'C++', 'C#', 'Go', 'Rust', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'R', 'Scala', 'Dart', 'SQL', 'Shell'},
    'Frontend': {'React', 'Next.js', 'Vue', 'Angular', 'Svelte', 'HTML', 'CSS', 'Tailwind', 'Sass', 'Redux', 'GraphQL', 'Webpack', 'Responsive Design'},
    'Backend & Cloud': {'Node.js', 'Express', 'FastAPI', 'Django', 'Flask', 'Spring Boot', 'NestJS', 'ASP.NET', 'REST API', 'Microservices', 'gRPC', 'Serverless', 'AWS', 'Azure', 'GCP', 'Docker', 'Kubernetes', 'Terraform', 'Ansible', 'CI/CD', 'Linux', 'Nginx'},
    'Databases': {'PostgreSQL', 'MySQL', 'MongoDB', 'Redis', 'SQLite', 'Cassandra', 'Elasticsearch', 'DynamoDB', 'Firebase', 'Supabase', 'Snowflake', 'BigQuery', 'Databricks'},
    'AI & Data': {'Machine Learning', 'Deep Learning', 'NLP', 'LLMs', 'LangChain', 'Computer Vision', 'PyTorch', 'TensorFlow', 'scikit-learn', 'Pandas', 'NumPy', 'SciPy', 'Data Analysis', 'Data Visualization', 'Statistics', 'Data Pipelines', 'Spark', 'Kafka', 'Hadoop'},
    'Practices & Soft Skills': {'Data Structures', 'Algorithms', 'System Design', 'OOP', 'Design Patterns', 'Unit Testing', 'TDD', 'Git', 'Agile', 'Cybersecurity', 'UI/UX', 'Figma', 'Product Management', 'Communication', 'Leadership', 'Problem Solving', 'Mentorship', 'Cross-functional'}
}

def load_skills():
    global SKILLS, SKILL_CATEGORY, PATTERNS
    SKILLS = {}
    SKILL_CATEGORY = {}
    cur_cat = 'General'
    skills_file = DATA / "skills.txt"
    if skills_file.exists():
        for line in skills_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                cur_cat = line.lstrip("#").strip()
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts:
                canonical = parts[0]
                SKILLS[canonical] = [p.lower() for p in parts]
                # Map category
                cat_found = False
                for cat, set_names in DEFAULT_CATEGORIES.items():
                    if canonical in set_names:
                        SKILL_CATEGORY[canonical] = cat
                        cat_found = True
                        break
                if not cat_found:
                    SKILL_CATEGORY[canonical] = cur_cat

    PATTERNS = {c: re.compile(r"(?<![\w+#])(?:" + "|".join(re.escape(a) for a in al) + r")(?![\w+#])", re.I)
                for c, al in SKILLS.items()}

load_skills()

# ---------- parsing ----------
class ResumeFileError(ValueError):
    pass


def extract_text(name: str, data: bytes) -> str:
    extension = pathlib.Path(name).suffix.lower()
    if extension not in {".pdf", ".docx", ".txt"}:
        raise ResumeFileError("Unsupported file type. Upload a PDF, DOCX or TXT resume.")
    if not data:
        raise ResumeFileError("The uploaded file is empty.")
    if extension == ".pdf":
        import pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:
            logger.exception("Could not extract text from PDF %r", name)
            raise ResumeFileError("Could not read this PDF. Check that it is not damaged or password-protected.") from exc
    if extension == ".docx":
        import docx
        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as exc:
            logger.exception("Could not extract text from DOCX %r", name)
            raise ResumeFileError("Could not read this DOCX. Check that it is a valid, unprotected Word document.") from exc
        parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        for section in document.sections:
            for area in (section.header, section.footer):
                parts.extend(p.text.strip() for p in area.paragraphs if p.text.strip())
                for table in area.tables:
                    for row in table.rows:
                        cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if cells:
                            parts.append(" | ".join(cells))
        return "\n".join(parts)
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError:
            return data.decode("latin-1")

def find_skills(text: str):
    """Returns (set of canonical skills, non-overlapping spans for highlighting)."""
    spans, found = [], set()
    for c, pat in PATTERNS.items():
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), c))
            found.add(c)
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    clean, end = [], -1
    for s in spans:
        if s[0] >= end:
            clean.append(s)
            end = s[1]
    return found, clean


def compare_job_keywords(job_text: str, resume_text: str, limit: int = 30) -> List[Dict[str, Any]]:
    resume_skills, _ = find_skills(resume_text)
    keywords = []
    seen = set()

    for canonical, pattern in PATTERNS.items():
        match = pattern.search(job_text)
        if match:
            term = match.group(0)
            normalized = re.sub(r"\W+", " ", term.lower()).strip()
            if normalized not in seen:
                keywords.append({"term": term, "skill": canonical, "present": canonical in resume_skills})
                seen.add(normalized)

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(2, 3),
            max_features=120,
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.-]{2,}\b",
        )
        scores = vectorizer.fit_transform([job_text]).toarray()[0]
        terms = vectorizer.get_feature_names_out()
    except ValueError:
        terms, scores = [], []

    resume_terms = " ".join(re.findall(r"[a-z0-9+#.-]+", resume_text.lower()))
    generic_terms = {
        "ability", "build", "building", "candidate", "developer", "engineer",
        "experience", "including", "job", "knowledge", "preferred", "required",
        "responsibilities", "role", "skills", "strong", "systems", "team",
        "work", "years",
    }
    for index in sorted(range(len(terms)), key=lambda i: (-scores[i], -len(terms[i].split()), terms[i])):
        term = terms[index]
        normalized = re.sub(r"\W+", " ", term.lower()).strip()
        meaningful_terms = [word for word in normalized.split() if word not in generic_terms]
        if not normalized or normalized in seen or len(meaningful_terms) < 2:
            continue
        phrase_pattern = r"(?<!\w)" + re.escape(term).replace(r"\ ", r"\s+") + r"(?!\w)"
        surface = re.search(phrase_pattern, job_text, re.I)
        if not surface:
            continue
        keywords.append({
            "term": surface.group(0),
            "skill": None,
            "present": f" {normalized} " in f" {resume_terms} ",
        })
        seen.add(normalized)
        if len(keywords) >= limit:
            break
    return keywords[:limit]


SKILL_LIST_HEADING = re.compile(
    r"^(?:skills?|technical skills|core competencies|languages|technologies|tools|expertise)\b",
    re.I,
)


def is_skill_inventory_line(text: str) -> bool:
    if SKILL_LIST_HEADING.match(text):
        return True
    verbs = set(re.findall(r"[a-z]+", text.lower())) & ACTION_VERBS
    return text.count(",") >= 4 and not verbs


def skill_evidence(text: str, skills: List[str], limit: int = 6) -> List[Dict[str, str]]:
    wanted = set(skills)
    evidence = []
    seen = set()
    for line in text.splitlines():
        clean = re.sub(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s*", "", line).strip()
        if is_skill_inventory_line(clean):
            continue
        found, _ = find_skills(clean)
        for skill in sorted(found & wanted):
            if skill not in seen and len(clean) >= 20:
                evidence.append({"skill": skill, "text": clean[:240]})
                seen.add(skill)
                if len(evidence) >= limit:
                    return evidence
    return evidence


def resolve_role_profile(query: str, jobs: List[Dict[str, Any]]):
    stop_words = {"a", "an", "as", "be", "become", "career", "experience", "for", "i", "in", "job", "looking", "my", "of", "role", "the", "to", "want", "with", "year", "years", "yrs"}
    role_aliases = {"developer": "technical_role", "engineer": "technical_role", "programmer": "technical_role"}

    def terms(value: str) -> Set[str]:
        normalized = re.sub(r"\bback[\s-]+end\b", "backend", value.lower())
        normalized = re.sub(r"\bfront[\s-]+end\b", "frontend", normalized)
        words = re.findall(r"[a-z0-9+#]+", normalized)
        return {
            role_aliases.get(word, word)
            for word in words
            if word not in stop_words and not word.isdigit()
        }

    query_terms = terms(query)
    if not query_terms:
        return None

    best_job, best_score = None, 0.0
    for job in jobs:
        title_terms = terms(job["title"])
        union = query_terms | title_terms
        score = len(query_terms & title_terms) / len(union) if union else 0.0
        if score > best_score:
            best_job, best_score = job, score
    return best_job if best_score >= 0.45 else None


def years_of_experience(text: str) -> float:
    explicit = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years|yrs)", text, re.I)]
    if explicit:
        return max(explicit)
    experience_headings = re.compile(
        r"^(?:(?:professional|work)\s+)?experience$|^(?:employment|work)\s+history$|^internships?$",
        re.I,
    )
    other_headings = re.compile(
        r"^(?:education|academics?|skills|projects|certifications?|awards|summary|profile|objective|publications|volunteering)$",
        re.I,
    )
    in_experience = False
    ranges = []
    current_year = datetime.datetime.now().year
    for line in text.splitlines():
        heading = re.sub(r"[:\s]+$", "", line.strip())
        if experience_headings.fullmatch(heading):
            in_experience = True
            continue
        if other_headings.fullmatch(heading):
            in_experience = False
        if not in_experience:
            continue
        for start, finish in re.findall(
            r"\b((?:19|20)\d{2})\s*(?:-|–|\bto\b)\s*((?:19|20)\d{2}|present|current)\b",
            line,
            re.I,
        ):
            start_year = int(start)
            end_year = current_year if finish.lower() in ("present", "current") else int(finish)
            if end_year > start_year:
                ranges.append((start_year, min(end_year, start_year + 35)))
    if not ranges:
        return 0.0
    ranges.sort()
    total = 0
    start, end = ranges[0]
    for next_start, next_end in ranges[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    total += end - start
    return float(min(total, 35))

def detect_seniority(years: float, text: str) -> str:
    low = text.lower()
    if any(k in low for k in ("director", "vp of engineering", "head of", "chief architect")):
        return "Director / Executive"
    if any(k in low for k in ("staff engineer", "principal engineer", "lead developer", "tech lead")):
        return "Lead / Principal"
    if years >= 6 or "senior" in low:
        return "Senior"
    if years >= 3:
        return "Mid-Level"
    if years >= 1:
        return "Junior"
    return "Entry-Level / Fresher"

# ---------- semantic similarity (MiniLM embeddings, TF-IDF fallback) ----------
_model = None


def _embedding_chunks(text: str, words_per_chunk: int = 180) -> List[str]:
    words = text.split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


def semantic(a: str, b: str):
    if not a.strip() or not b.strip():
        return 0.0, "No text to compare"
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as exc:
            logger.warning("MiniLM unavailable; using TF-IDF fallback: %s", exc)
            _model = False
    if _model:
        resume_chunks, job_chunks = _embedding_chunks(a), _embedding_chunks(b)
        embeddings = _model.encode(resume_chunks + job_chunks, normalize_embeddings=True, batch_size=32)
        resume_vectors = embeddings[:len(resume_chunks)]
        job_vectors = embeddings[len(resume_chunks):]
        best_matches = [
            max(float(resume_vector @ job_vector) for resume_vector in resume_vectors)
            for job_vector in job_vectors
        ]
        cosine = sum(best_matches) / len(best_matches)
        return max(0.0, min(1.0, (cosine - 0.15) / 0.55)), "MiniLM Neural Embeddings"
    from sklearn.feature_extraction.text import TfidfVectorizer
    m = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([a, b])
    return min(1.0, float((m[0] @ m[1].T).toarray()[0, 0]) * 2.5), "TF-IDF N-Gram Fallback"

def match(resume_text, resume_skills, resume_years, job_text, job_skills, min_years=0, weights=None):
    sem, method = semantic(resume_text, job_text)
    matched = sorted(set(resume_skills) & set(job_skills))
    missing = sorted(set(job_skills) - set(resume_skills))
    ov = len(matched) / len(job_skills) if job_skills else None
    exp = min(1.0, resume_years / min_years) if min_years > 0 else None
    configured_weights = weights or {"semantic": W_SEM, "skills": W_SKILL, "experience": W_EXP}
    available_scores = {"semantic": sem}
    if ov is not None:
        available_scores["skills"] = ov
    if exp is not None:
        available_scores["experience"] = exp
    weighted_scores = [
        (configured_weights.get(name, 0), value)
        for name, value in available_scores.items()
        if configured_weights.get(name, 0) > 0
    ]
    if not weighted_scores:
        raise ValueError("At least one available scoring factor must have a positive weight.")
    total_weight = sum(weight for weight, _ in weighted_scores)
    score = 100 * sum(weight * value for weight, value in weighted_scores) / total_weight
    score_weights = {name: round(configured_weights.get(name, 0) / total_weight * 100) if name in available_scores else 0
                     for name in ("semantic", "skills", "experience")}
    
    # Categorize matched and missing
    matched_by_cat = {}
    for s in matched:
        cat = SKILL_CATEGORY.get(s, "General")
        matched_by_cat.setdefault(cat, []).append(s)
        
    return {
        "score": round(score, 1),
        "matched": matched,
        "missing": missing,
        "matched_by_cat": matched_by_cat,
        "method": method,
        "weights": score_weights,
        "breakdown": {
            "semantic": round(sem * 100),
            "skills": round(ov * 100) if ov is not None else None,
            "experience": round(exp * 100) if exp is not None else None
        }
    }

# ---------- ATS score with granular feedback ----------
ACTION_VERBS = {
    "built", "developed", "designed", "led", "created", "implemented", "improved",
    "reduced", "increased", "deployed", "optimized", "launched", "managed",
    "analyzed", "automated", "spearheaded", "architected", "delivered", "orchestrated",
    "streamlined", "accelerated", "scaled", "mentored", "initiated", "engineered"
}

def ats(text: str):
    low = text.lower()
    tips = []
    
    # Category 1: Contact Information (15 pts)
    has_email = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))
    has_phone = bool(re.search(r"\+?\d[\d\s-]{8,}", text))
    has_links = bool(re.search(r"github\.com|linkedin\.com|gitlab\.com|portfolio|\.dev", low))
    contact_score = (7 if has_email else 0) + (5 if has_phone else 0) + (3 if has_links else 0)
    if not has_email: tips.append({"priority": "high", "text": "Missing professional email address."})
    if not has_phone: tips.append({"priority": "high", "text": "Missing telephone contact number."})
    if not has_links: tips.append({"priority": "medium", "text": "Include your GitHub, LinkedIn, or personal portfolio link."})

    # Category 2: Core Sections (25 pts)
    has_edu = "education" in low or "academic" in low
    has_exp = any(k in low for k in ("experience", "internship", "employment", "work history"))
    has_proj = "project" in low or "portfolio" in low
    has_skills_sec = "skills" in low or "technologies" in low or "technical expertise" in low
    sections_score = (7 if has_edu else 0) + (8 if has_exp else 0) + (5 if has_proj else 0) + (5 if has_skills_sec else 0)
    if not has_edu: tips.append({"priority": "high", "text": "Add a dedicated 'Education' section."})
    if not has_exp: tips.append({"priority": "high", "text": "Add an 'Experience' or 'Work History' section."})
    if not has_proj: tips.append({"priority": "medium", "text": "Add a 'Projects' section to showcase applied technical skills."})
    if not has_skills_sec: tips.append({"priority": "high", "text": "Include an explicit 'Technical Skills' section for ATS parsers."})

    # Category 3: Measurable Impact & Quantifiable Metrics (25 pts)
    metric_matches = re.findall(r"\d+\s*%|\$\d+[\d,]*|\b\d+x\b|\b\d+\s*(?:ms|seconds|minutes|users|clients|requests|million|billion|k)\b", text, re.I)
    metric_count = len(metric_matches)
    metrics_score = min(25, metric_count * 5)
    if metric_count < 3:
        tips.append({"priority": "high", "text": f"Found only {metric_count} metric(s). Quantify your achievements (e.g., 'reduced API latency by 45%', 'scaled to 50k users')."})

    # Category 4: Strong Action Verbs (20 pts)
    found_verbs = [v for v in ACTION_VERBS if re.search(r"\b" + v + r"\b", low)]
    verbs_score = min(20, len(found_verbs) * 4)
    if len(found_verbs) < 4:
        tips.append({"priority": "medium", "text": "Start bullet points with assertive action verbs (e.g., Engineered, Spearheaded, Automated, Optimized)."})

    # Category 5: Length & Readability (15 pts)
    words = len(text.split())
    if 300 <= words <= 950:
        length_score = 15
    elif 200 <= words < 300 or 950 < words <= 1300:
        length_score = 9
        tips.append({"priority": "low", "text": f"Resume is {words} words. Aim for 350-900 words (1-2 pages) for optimal ATS scanning."})
    else:
        length_score = 4
        tips.append({"priority": "high", "text": f"Resume length ({words} words) is suboptimal. Avoid under 200 or over 1300 words."})

    total_score = min(100, contact_score + sections_score + metrics_score + verbs_score + length_score)
    
    categories = {
        "contact": {"score": contact_score, "max": 15},
        "sections": {"score": sections_score, "max": 25},
        "metrics": {"score": metrics_score, "max": 25},
        "verbs": {"score": verbs_score, "max": 20},
        "length": {"score": length_score, "max": 15}
    }
    
    tip_strings = [t["text"] for t in tips]
    return total_score, tip_strings, categories

# ---------- Bias-Free Anonymization (DEI Blind Screening) ----------
def anonymize(text: str) -> str:
    # Mask emails & phones
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[candidate-email-hidden]", text)
    text = re.sub(r"\+?\d[\d\s-]{8,}", "[candidate-phone-hidden]", text)
    # Mask LinkedIn / URLs
    text = re.sub(r"https?://(?:www\.)?linkedin\.com/in/[\w-]+", "[linkedin-hidden]", text, flags=re.I)
    text = re.sub(r"https?://(?:www\.)?github\.com/[\w-]+", "[github-hidden]", text, flags=re.I)
    # Mask graduation years to prevent age bias
    text = re.sub(r"\b(?:class of|graduated in|batch of)\s*(?:19|20)\d{2}\b", "[grad-year-hidden]", text, flags=re.I)
    # Mask prominent universities to prevent prestige bias
    univ_pat = r"\b(?:University|College|Institute of Technology|IIT|NIT|BITS|Stanford|MIT|Harvard|Berkeley|Oxford|Cambridge)\b[^\n,.]*"
    text = re.sub(univ_pat, "[institution-hidden]", text, flags=re.I)
    # Mask candidate name in header
    lines = text.splitlines()
    if lines:
        lines[0] = "[Candidate Name Redacted for Blind Screening]"
    return "\n".join(lines)

# ---------- Smart Bullet Rewriter (Heuristic + LLM fallback) ----------
WEAK_PATTERNS = [
    (r"^(?:responsible for|worked on|helped with|assisted in|handled)\s+(.*)",
     "Delivered {rest}. Add the verified result: [measurable outcome]."),
    (r"^(?:used|using)\s+([\w\s,]+)\s+to\s+(.*)",
     "Applied {first} to {rest}. Add the verified result: [measurable outcome]."),
    (r"^(?:participated in|contributed to)\s+(.*)",
     "Contributed to {rest}. Specify your role and add the verified result: [measurable outcome]."),
    (r"^(?:fixed|resolved)\s+(?:bugs|issues)\s*(.*)",
     "Investigated and resolved {rest}. Add the verified impact: [measurable outcome].")
]

def heuristic_rewrite(bullet: str) -> str:
    cleaned = bullet.strip().lstrip("-*•").strip()
    if not cleaned:
        return ""
    low = cleaned.lower()
    metric_pattern = r"\d+\s*%|\$\d+[\d,]*|\b\d+x\b|\b\d+\s*(?:ms|seconds|minutes|users|clients|requests|million|billion|k)\b"
    passive_patterns = (
        (r"^responsible for\s+", "Managed "),
        (r"^worked on\s+", "Engineered "),
        (r"^helped with\s+", "Contributed to "),
        (r"^assisted in\s+", "Supported "),
        (r"^handled\s+", "Managed "),
        (r"^participated in\s+", "Contributed to "),
        (r"^contributed to\s+", "Delivered "),
    )
    suggested = cleaned
    used = re.match(r"^(?:used|using)\s+(.+?)\s+to\s+(.*)", cleaned, re.I)
    if used:
        suggested = f"Applied {used.group(1)} to {used.group(2)}"
    else:
        for pattern, replacement in passive_patterns:
            match = re.match(pattern, cleaned, re.I)
            if match:
                suggested = replacement + cleaned[match.end():].lstrip(" ,;:")
                break
        else:
            first_word = cleaned.split()[0].lower() if cleaned.split() else ""
            if first_word not in ACTION_VERBS:
                if any(term in low for term in ("latency", "performance", "efficiency", "optimiz")):
                    verb = "Optimized"
                elif any(term in low for term in ("autom", "workflow", "script")):
                    verb = "Automated"
                elif any(term in low for term in ("architect", "system", "platform", "design")):
                    verb = "Architected"
                else:
                    verb = "Engineered"
                suggested = f"{verb} {cleaned[0].lower() + cleaned[1:]}"
    if not re.search(metric_pattern, cleaned, re.I):
        suggested += " — add a verified impact: [measurable outcome]"
    return suggested


REWRITE_SECTIONS = {
    "experience": re.compile(r"^(?:(?:professional|work)\s+)?experience$|^(?:employment|work)\s+history$|^internships?$", re.I),
    "projects": re.compile(r"^(?:selected|personal|academic|relevant)?\s*projects?$", re.I),
    "objective": re.compile(r"^(?:career\s+)?objective$", re.I),
}
NON_REWRITE_HEADINGS = re.compile(
    r"^(?:education|academics?|skills|technical skills|core skills|languages|coursework|relevant coursework|certifications?|awards|summary|professional summary|profile|publications|volunteering|interests|additional information)$",
    re.I,
)


def rewrite_suggestions(text: str, limit: int = 12) -> List[Dict[str, Any]]:
    suggestions = []
    section = None
    bullet_pattern = re.compile(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s+")
    weak_opening = re.compile(
        r"^(?:responsible for|worked on|helped with|assisted in|handled|participated in|contributed to|used|using)\b",
        re.I,
    )
    impact_pattern = re.compile(
        r"\$\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*\+?\s*(?:%|x\b|ms\b|seconds?\b|minutes?\b|users?\b|clients?\b|customers?\b|requests?\b|million\b|billion\b|k\b|students?\b|records?\b|teammates?\b)",
        re.I,
    )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.sub(r"[:\s]+$", "", stripped)
        section = next((name for name, pattern in REWRITE_SECTIONS.items() if pattern.fullmatch(heading)), section)
        if section and any(pattern.fullmatch(heading) for pattern in REWRITE_SECTIONS.values()):
            continue
        if NON_REWRITE_HEADINGS.fullmatch(heading):
            section = None
            continue
        if section not in REWRITE_SECTIONS:
            continue

        original = bullet_pattern.sub("", stripped).strip()
        if len(original) < 30:
            continue
        explicit_bullet = bool(bullet_pattern.match(stripped))
        starts_with_verb = original.split()[0].lower() in ACTION_VERBS
        is_weak = bool(weak_opening.match(original))
        if not (explicit_bullet or starts_with_verb or is_weak):
            continue

        reasons = []
        if is_weak:
            reasons.append("Replace the passive or vague opening with a stronger action verb.")
        elif not starts_with_verb:
            reasons.append("Start with a clear action verb.")
        has_impact = bool(impact_pattern.search(original))
        if not has_impact:
            reasons.append("Add a measurable result only if you can verify it.")
        if not reasons:
            continue

        suggested = heuristic_rewrite(original)
        if suggested == original:
            continue
        original_skills, _ = find_skills(original)
        suggested_skills, _ = find_skills(suggested)
        suggestions.append({
            "section": section.title(),
            "original": original,
            "suggested": suggested,
            "reasons": reasons,
            "action_score": int(starts_with_verb and not is_weak),
            "impact_score": int(has_impact),
            "added_keywords": sorted(suggested_skills - original_skills),
        })
        if len(suggestions) >= limit:
            break
    return suggestions


def create_updated_docx(text: str, edits: List[Dict[str, str]]) -> io.BytesIO:
    from docx import Document
    from docx.shared import Inches, Pt

    replacements = {edit["original"].strip(): edit["suggested"].strip() for edit in edits}
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)

    known_headings = re.compile(
        r"^(?:professional\s+)?(?:experience|work history|employment|education|academics?|skills|technical skills|projects?|objective|career objective|summary|certifications?|awards|coursework|relevant coursework)$",
        re.I,
    )
    bullet_prefix = re.compile(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s+")
    first_content = True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.sub(r"[:\s]+$", "", stripped)
        if known_headings.fullmatch(heading) or (len(heading) <= 48 and heading.isupper() and any(ch.isalpha() for ch in heading)):
            document.add_heading(heading, level=1)
            first_content = False
            continue
        content = bullet_prefix.sub("", stripped).strip()
        content = replacements.get(content, content)
        is_bullet = bool(bullet_prefix.match(stripped))
        paragraph = document.add_paragraph(style="List Bullet" if is_bullet else "Normal")
        paragraph.add_run(content)
        if first_content:
            paragraph.style = document.styles["Title"]
            first_content = False

    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


def generate_outreach(resume_text: str, job_title: str, company: str, job_text: str) -> Dict[str, str]:
    resume_skills, _ = find_skills(resume_text)
    job_skills, _ = find_skills(job_text)
    matched = sorted(resume_skills & job_skills)
    evidence = []
    for line in resume_text.splitlines():
        clean = re.sub(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s*", "", line).strip()
        if is_skill_inventory_line(clean):
            continue
        line_skills, _ = find_skills(clean)
        if len(clean) >= 35 and line_skills & set(matched):
            evidence.append(clean)
        if len(evidence) == 2:
            break
    role = job_title.strip() or "the role"
    organization = company.strip() or "your team"
    skills_text = ", ".join(matched[:5]) if matched else "the skills and projects described in my resume"
    evidence_text = " ".join(f'For example, "{item}"' for item in evidence)
    if not evidence_text:
        evidence_text = f"My resume lists relevant experience with {skills_text}."
    body = "\n\n".join([
        f"Dear Hiring Team,\nI’m reaching out to express my interest in the {role} opportunity at {organization}. My resume highlights {skills_text}.",
        f"{evidence_text} I would be glad to discuss how this documented experience aligns with the role’s requirements.",
        f"Thank you for your time and consideration. I’d welcome the opportunity to talk further about the {role} role at {organization}.\nBest regards",
    ])
    return {"subject": f"Application for {role}", "body": body}


def rewrite_bullets(text: str) -> str:
    # 1. Try Anthropic if key exists
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            r = client.messages.create(
                model=os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": "Rewrite the 4 weakest bullet points from this resume using strong action verbs and Google's XYZ formula (Accomplished [X] as measured by [Y], by doing [Z]). Use [X] placeholders for metrics where missing:\n\n" + text[:4500]
                }]
            )
            return r.content[0].text
        except Exception as exc:
            logger.warning("Anthropic bullet rewrite failed; trying the next available method: %s", exc)

    # 2. Try OpenAI if key exists
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=800,
                messages=[
                    {"role": "system", "content": "You are an elite ATS resume coach. Rewrite weak bullets using the XYZ impact framework."},
                    {"role": "user", "content": "Rewrite the 4 weakest bullet points from this resume with measurable impact and bold action verbs:\n\n" + text[:4500]}
                ]
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("OpenAI bullet rewrite failed; using the rule-based fallback: %s", exc)

    # 3. Intelligent Heuristic Rule-Based Optimizer (Default, zero API key required)
    lines = [line.strip().lstrip("-*• ") for line in text.splitlines() if len(line.strip()) > 35]
    weak_bullets = []
    for l in lines:
        if any(w in l.lower() for w in ("responsible for", "worked on", "helped", "handled", "participated", "assisted")):
            weak_bullets.append(l)
        elif not re.search(r"\d", l) and len(l) > 40:
            weak_bullets.append(l)
    
    if not weak_bullets:
        weak_bullets = lines[:4]
    
    selected = weak_bullets[:4]
    output_lines = [
        "### Rule-based resume bullet suggestions",
        "These suggestions do not invent results. Replace bracketed placeholders only with verified outcomes.\n"
    ]
    
    for i, orig in enumerate(selected, 1):
        improved = heuristic_rewrite(orig)
        output_lines.append(f"**Bullet #{i} Original:**\n> {orig}\n")
        output_lines.append(f"**✨ ATS-Optimized Version:**\n> {improved}\n")
        output_lines.append("---")
        
    output_lines.append("\n*Tip: Replace `[measurable outcome]` with a result you can verify before applying.*")
    return "\n".join(output_lines)

# ---------- Interview Prep Generator ----------
def interview_prep(job_title: str, matched_skills: List[str], missing_skills: List[str], years: float) -> List[Dict[str, str]]:
    questions = []
    
    # Missing skill gap questions
    if missing_skills:
        for skill in missing_skills[:2]:
            questions.append({
                "type": "Skill Gap Ramp-Up",
                "tag": skill,
                "question": f"The {job_title} role requires experience with {skill}. How have you approached quickly mastering new technologies or tools in past projects?",
                "guidance": f"Highlight conceptual parallels from your experience with {', '.join(matched_skills[:2]) if matched_skills else 'similar tools'}, discuss tutorials or side projects, and showcase your fast learning curve."
            })
            
    # Technical depth on matched skills
    if matched_skills:
        for skill in matched_skills[:2]:
            questions.append({
                "type": "Core Technical Deep Dive",
                "tag": skill,
                "question": f"In your experience utilizing {skill}, describe a challenging technical bottleneck or architectural trade-off you encountered and how you resolved it.",
                "guidance": f"Structure your answer using STAR (Situation, Task, Action, Result). Mention performance benchmarks, code maintainability, or data integrity."
            })
            
    # System design / Seniority question
    if years >= 3:
        questions.append({
            "type": "System Architecture & Scalability",
            "tag": "Architecture",
            "question": f"How would you design a highly available, fault-tolerant backend service that powers {job_title} operations at 10x current scale?",
            "guidance": "Discuss caching layers (Redis), asynchronous queues (Kafka/RabbitMQ), load balancing, horizontal pod autoscaling, and database sharding or indexing strategies."
        })
    else:
        questions.append({
            "type": "Problem Solving & Fundamentals",
            "tag": "Algorithms & Code Quality",
            "question": "Can you walk through your process for debugging a subtle bug in production? What tools, logs, and testing strategies do you employ?",
            "guidance": "Demonstrate systematic thinking: reproducing the issue, reading stack traces, isolating variables, writing a regression test, and preventing future occurrences."
        })

    # Behavioral culture fit
    questions.append({
        "type": "Behavioral & Collaboration",
        "tag": "Teamwork",
        "question": "Tell me about a time when you disagreed with a teammate or stakeholder on an engineering or project decision. How was it resolved?",
        "guidance": "Emphasize mutual respect, relying on objective data/prototypes rather than ego, and committing fully to the team's shared outcome."
    })
    
    return questions

"""NLP + ML engine: parsing, skill extraction, hybrid matching, ATS scoring, and interview prep."""
import io, re, os, pathlib
from typing import List, Dict, Tuple, Set, Any

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
W_SEM, W_SKILL, W_EXP = 0.40, 0.40, 0.20   # hybrid score weights

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
def extract_text(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    if n.endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    return data.decode("utf-8", "ignore")

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

def years_of_experience(text: str) -> float:
    explicit = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\+?\s*(?:years|yrs)", text, re.I)]
    if explicit:
        return max(explicit)
    total = 0
    # Current year dynamic fallback
    import datetime
    current_year = datetime.datetime.now().year
    for a, b in re.findall(r"((?:19|20)\d{2})\s*[-–to]+\s*((?:19|20)\d{2}|present|current)", text, re.I):
        end = current_year if b.lower() in ("present", "current") else int(b)
        total += max(0, min(end - int(a), 12))
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
def semantic(a: str, b: str):
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _model = False
    if _model:
        e = _model.encode([a[:4000], b[:4000]], normalize_embeddings=True)
        return max(0.0, min(1.0, (float(e[0] @ e[1]) - 0.15) / 0.55)), "MiniLM Neural Embeddings"
    from sklearn.feature_extraction.text import TfidfVectorizer
    m = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([a, b])
    return min(1.0, float((m[0] @ m[1].T).toarray()[0, 0]) * 2.5), "TF-IDF N-Gram Fallback"

def match(resume_text, resume_skills, resume_years, job_text, job_skills, min_years=0):
    sem, method = semantic(resume_text, job_text)
    matched = sorted(set(resume_skills) & set(job_skills))
    missing = sorted(set(job_skills) - set(resume_skills))
    ov = len(matched) / len(job_skills) if job_skills else sem
    exp = min(1.0, resume_years / min_years) if min_years else 1.0
    score = 100 * (W_SEM * sem + W_SKILL * ov + W_EXP * exp)
    
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
        "breakdown": {
            "semantic": round(sem * 100),
            "skills": round(ov * 100),
            "experience": round(exp * 100)
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
    metric_matches = re.findall(r"\d+%\b|\$\d+[\d,]*|\b\d+x\b|\b\d+\s*(?:ms|seconds|minutes|users|clients|requests|million|billion|k)\b", text, re.I)
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
     "Engineered and delivered {rest}, achieving [X% efficiency increase] across [Y systems]."),
    (r"^(?:used|using)\s+([\w\s,]+)\s+to\s+(.*)",
     "Spearheaded the development of {rest} leveraging {first}, resulting in [X% faster processing / $Y cost savings]."),
    (r"^(?:participated in|contributed to)\s+(.*)",
     "Collaborated with cross-functional teams to build {rest}, accelerating sprint delivery by [X weeks]."),
    (r"^(?:fixed|resolved)\s+(?:bugs|issues)\s*(.*)",
     "Identified root causes and resolved critical bottlenecks in {rest}, reducing defect rate by [X%] and boosting uptime to 99.9%.")
]

def heuristic_rewrite(bullet: str) -> str:
    cleaned = bullet.strip().lstrip("-*•").strip()
    if not cleaned:
        return ""
    low = cleaned.lower()
    
    # Check weak patterns
    for pat, template in WEAK_PATTERNS:
        m = re.match(pat, low)
        if m:
            groups = m.groups()
            if len(groups) == 1:
                return template.format(rest=cleaned[m.start(1):m.end(1)])
            elif len(groups) == 2:
                return template.format(first=cleaned[m.start(1):m.end(1)], rest=cleaned[m.start(2):m.end(2)])

    # General transformation
    first_word = cleaned.split()[0] if cleaned.split() else ""
    if first_word.lower() not in ACTION_VERBS:
        return f"Architected and deployed {cleaned[:1].lower() + cleaned[1:]}, measuring a [25%+] improvement in throughput."
    
    if not re.search(r"\d+%", cleaned):
        return f"{cleaned} — resulting in [30%] reduction in processing time and enhanced reliability."
        
    return cleaned

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
        except Exception:
            pass

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
        except Exception:
            pass

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
        "### ⚡ AI Impact Bullet Optimizer (Action-Oriented & Quantified)",
        "Here are optimized rewrites following Google's XYZ formula: *Accomplished [X], as measured by [Y], by doing [Z]*.\n"
    ]
    
    for i, orig in enumerate(selected, 1):
        improved = heuristic_rewrite(orig)
        output_lines.append(f"**Bullet #{i} Original:**\n> {orig}\n")
        output_lines.append(f"**✨ ATS-Optimized Version:**\n> {improved}\n")
        output_lines.append("---")
        
    output_lines.append("\n*Tip: Replace bracketed metrics (e.g. `[X%]`) with your actual data points before applying!*")
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

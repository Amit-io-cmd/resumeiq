import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import db
db.init()


def make_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(output)


class ResumeExtractionTests(unittest.TestCase):
    def test_extracts_selectable_pdf_text(self):
        text = engine.extract_text("resume.pdf", make_pdf(
            "Experienced Python engineer with backend, API, database, and cloud delivery skills."
        ))
        self.assertIn("Python engineer", text)

    def test_extracts_docx_paragraphs_and_tables(self):
        document = Document()
        document.add_paragraph("Candidate resume")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Python"
        table.cell(0, 1).text = "FastAPI"
        buffer = io.BytesIO()
        document.save(buffer)

        text = engine.extract_text("resume.docx", buffer.getvalue())
        self.assertIn("Candidate resume", text)
        self.assertIn("Python | FastAPI", text)

    def test_decodes_utf16_text_and_rejects_unsupported_extensions(self):
        self.assertEqual(engine.extract_text("resume.txt", "Résumé".encode("utf-16")), "Résumé")
        with self.assertRaises(engine.ResumeFileError):
            engine.extract_text("resume.doc", b"legacy Word file")


class MatchScoringTests(unittest.TestCase):
    def test_normalizes_weights_when_experience_is_not_specified(self):
        with patch.object(engine, "semantic", return_value=(0.8, "test embeddings")):
            result = engine.match(
                "Python developer", {"Python"}, 0, "Python and SQL developer",
                {"Python", "SQL"}
            )
        self.assertEqual(result["score"], 65.0)
        self.assertIsNone(result["breakdown"]["experience"])
        self.assertEqual(result["weights"], {"semantic": 50, "skills": 50, "experience": 0})

    def test_does_not_double_count_semantics_when_no_skills_are_recognized(self):
        with patch.object(engine, "semantic", return_value=(0.6, "test embeddings")):
            result = engine.match("Resume text", set(), 0, "Unrecognized role text", set())
        self.assertEqual(result["score"], 60.0)
        self.assertIsNone(result["breakdown"]["skills"])
        self.assertEqual(result["weights"], {"semantic": 100, "skills": 0, "experience": 0})

    def test_neural_similarity_includes_resume_text_beyond_first_chunk(self):
        class FakeModel:
            inputs = []

            def encode(self, texts, normalize_embeddings, batch_size):
                self.inputs = texts
                return np.tile(np.array([[1.0, 0.0]]), (len(texts), 1))

        model = FakeModel()
        with patch.object(engine, "_model", model):
            score, method = engine.semantic(" ".join(["Python"] * 181 + ["FULLRESUMEEND"]), "Python role")
        self.assertTrue(any("FULLRESUMEEND" in chunk for chunk in model.inputs))
        self.assertEqual(score, 1.0)
        self.assertEqual(method, "MiniLM Neural Embeddings")


class ResumeFeedbackTests(unittest.TestCase):
    def test_experience_dates_are_limited_to_work_sections(self):
        sample = (Path(__file__).resolve().parents[2] / "data" / "sample_resume.txt").read_text(encoding="utf-8")
        self.assertEqual(engine.years_of_experience(sample), 0.0)

        overlapping_roles = """EXPERIENCE
Software Engineer 2020 - 2023
Consultant 2022 - 2024
EDUCATION
Degree 2015 - 2019
"""
        self.assertEqual(engine.years_of_experience(overlapping_roles), 4.0)

    def test_percentage_metrics_are_counted(self):
        _, _, categories = engine.ats("Improved model accuracy by 18%.")
        self.assertEqual(categories["metrics"]["score"], 5)

    def test_rewrite_never_invents_achievement_numbers(self):
        suggestion = engine.heuristic_rewrite("Responsible for data migration")
        self.assertIn("[measurable outcome]", suggestion)
        self.assertNotRegex(suggestion, r"\d+%|99\.9")

    def test_role_only_goal_resolves_to_the_closest_role_profile(self):
        jobs = [
            {"title": "Full Stack Developer"},
            {"title": "Backend Engineer"},
            {"title": "Data Analyst"},
        ]
        role = engine.resolve_role_profile("I want to be a backend developer", jobs)
        self.assertEqual(role["title"], "Backend Engineer")
        role_with_experience = engine.resolve_role_profile(
            "I want to be a backend developer with 2 years experience", jobs
        )
        self.assertEqual(role_with_experience["title"], "Backend Engineer")
        self.assertIsNone(engine.resolve_role_profile("I want a good job", jobs))


class NewFeatureTests(unittest.TestCase):
    def test_keyword_comparison_marks_taxonomy_terms_as_present_or_missing(self):
        keywords = engine.compare_job_keywords(
            "We need Python, PostgreSQL, and experience building distributed systems.",
            "Built Python services using PostgreSQL.",
        )
        by_skill = {item["skill"]: item["present"] for item in keywords if item["skill"]}
        self.assertTrue(by_skill["Python"])
        self.assertTrue(by_skill["PostgreSQL"])
        self.assertFalse(by_skill["Microservices"])
        self.assertNotIn("systems experience", [item["term"].lower() for item in keywords])

    def test_rewriter_only_suggests_bullets_in_target_sections(self):
        resume = """Languages: Python, JavaScript, SQL
Relevant Coursework
- Built a compiler project for a university course.
EXPERIENCE
- Responsible for maintaining Python services used by internal teams.
PROJECTS
- Worked on an API platform using PostgreSQL and Docker.
EDUCATION
- Developed a graduation project for the computer science program.
"""
        suggestions = engine.rewrite_suggestions(resume)
        self.assertEqual([item["section"] for item in suggestions], ["Experience", "Projects"])
        self.assertTrue(suggestions[0]["suggested"].startswith("Managed"))
        self.assertIn("[measurable outcome]", suggestions[0]["suggested"])

    def test_rewriter_recognizes_plus_metrics_and_evidence_skips_skill_inventories(self):
        suggestions = engine.rewrite_suggestions(
            "EXPERIENCE\n- Built Python services used by 5,000+ customers."
        )
        self.assertEqual(suggestions, [])
        resume = (
            "SKILLS Python, PostgreSQL, Docker, Redis, FastAPI\n"
            "Built Python APIs for internal teams and improved response times."
        )
        evidence = engine.skill_evidence(resume, ["Python"])
        self.assertEqual(len(evidence), 1)
        self.assertTrue(evidence[0]["text"].startswith("Built"))
        outreach = engine.generate_outreach(resume, "Backend Engineer", "Example", "Python backend role")
        self.assertNotIn("SKILLS Python", outreach["body"])

    def test_custom_weights_and_outreach_paragraphs(self):
        with patch.object(engine, "semantic", return_value=(0.8, "test embeddings")):
            result = engine.match(
                "Python developer", {"Python"}, 2, "Python and SQL developer",
                {"Python", "SQL"}, min_years=4,
                weights={"semantic": 0, "skills": 100, "experience": 0},
            )
        self.assertEqual(result["score"], 50.0)
        self.assertEqual(result["weights"], {"semantic": 0, "skills": 100, "experience": 0})

        draft = engine.generate_outreach(
            "Python engineer with experience building services.\nBuilt Python APIs for internal teams.",
            "Backend Engineer", "Example Co", "Seeking a Python engineer to build backend APIs.",
        )
        self.assertEqual(len(draft["body"].split("\n\n")), 3)
        self.assertIn("Python", draft["body"])

    def test_docx_export_applies_edited_bullet(self):
        source = "EXPERIENCE\n- Responsible for building Python services."
        output = engine.create_updated_docx(source, [{
            "original": "Responsible for building Python services.",
            "suggested": "Managed Python services and improved reliability.",
        }])
        document = Document(output)
        content = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("Managed Python services and improved reliability.", content)
        self.assertNotIn("Responsible for building Python services.", content)


class NewFeaturesTests(unittest.TestCase):
    def test_pure_python_tfidf_similarity(self):
        sim = engine._pure_python_tfidf_similarity(
            "Python FastAPI Docker backend developer",
            "Python FastAPI backend engineer microservices"
        )
        self.assertGreater(sim, 0.4)
        zero_sim = engine._pure_python_tfidf_similarity("apple orange banana", "car truck motorcycle")
        self.assertEqual(zero_sim, 0.0)

    def test_predict_top_roles(self):
        jobs = [
            {"id": 1, "title": "Python Developer", "skills": ["Python", "FastAPI", "SQL"], "description": "Python APIs", "min_years": 1},
            {"id": 2, "title": "Frontend React Dev", "skills": ["React", "JavaScript", "CSS"], "description": "React UI", "min_years": 1}
        ]
        text = "Experienced Python developer skilled in Python, FastAPI, and SQL databases. Built REST APIs."
        skills = {"Python", "FastAPI", "SQL"}
        roles = engine.predict_top_roles(text, skills, 2.0, jobs, top_n=2)
        self.assertEqual(len(roles), 2)
        self.assertEqual(roles[0]["title"], "Python Developer")
        self.assertGreater(roles[0]["score"], roles[1]["score"])
        self.assertIn("why_it_fits", roles[0])

    def test_get_study_links_curated_fallback(self):
        links = engine.get_study_links_for_skills(["Python", "Docker"])
        self.assertEqual(len(links), 2)
        self.assertTrue(all("url" in item and "title" in item and "channel" in item for item in links))


if __name__ == "__main__":
    unittest.main()

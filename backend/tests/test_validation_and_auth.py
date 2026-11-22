import io
import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import engine as E
import db
from main import app

db.init()
client = TestClient(app)


class ResumeValidationTests(unittest.TestCase):
    def setUp(self):
        sample_path = BACKEND_DIR.parent / "data" / "sample_resume.txt"
        self.sample_resume = sample_path.read_text(encoding="utf-8")

    def test_valid_technical_resume_passes(self):
        is_valid, err_msg, warnings, details = E.validate_resume_text(self.sample_resume)
        self.assertTrue(is_valid)
        self.assertEqual(err_msg, "")
        self.assertGreater(details["skills_found"], 5)
        self.assertTrue(details["has_section"])
        self.assertTrue(details["has_contact"])

    def test_too_short_text_rejected(self):
        short_text = "I am a programmer looking for a job. Hire me please."
        is_valid, err_msg, warnings, details = E.validate_resume_text(short_text)
        self.assertFalse(is_valid)
        self.assertIn("too brief", err_msg.lower())
        self.assertEqual(details["reason"], "too_short")

    def test_vowelless_gibberish_rejected(self):
        gibberish = "dfghjkl zxcvbnm qwrtypsdfghjkl mnbvcxz lkjhgfdsapoiu dfghjk zxcvbnm qwrtypsdf " * 4
        is_valid, err_msg, warnings, details = E.validate_resume_text(gibberish)
        self.assertFalse(is_valid)
        self.assertTrue(any("character sequence" in w.lower() or "random" in w.lower() for w in warnings))

    def test_non_resume_content_without_structural_markers_rejected(self):
        story = (
            "The quick brown fox jumps over the lazy dog repeatedly on a sunny afternoon in the forest. "
            "Birds are singing happily in the trees while the river gently flows down the rocky valley. "
            "There were several colorful butterflies flying near the fragrant wildflowers on the hill."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(story)
        self.assertFalse(is_valid)
        self.assertIn("does not appear to be a technical resume", err_msg)

    def test_partial_resume_passes_with_warnings(self):
        partial = (
            "Summary: Motivated Software Developer with experience in web applications.\n"
            "Technical Skills: Python, SQL, Git, Linux, Docker, REST APIs.\n"
            "Projects: Developed web scrapers and internal data pipelines for automation and process improvements.\n"
            "Experience: Built microservices and automated deployment scripts using modern developer tools."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(partial)
        self.assertTrue(is_valid)
        self.assertGreater(len(warnings), 0)
        self.assertTrue(any("contact" in w.lower() or "brief" in w.lower() for w in warnings))


class AuthAndEndpointsTests(unittest.TestCase):
    def setUp(self):
        sample_path = BACKEND_DIR.parent / "data" / "sample_resume.txt"
        self.sample_resume = sample_path.read_text(encoding="utf-8")

    def test_auth_config_endpoint(self):
        res = client.get("/auth/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("google_client_id", data)
        self.assertIn("dev_mode", data)

    def test_dev_login_and_auth_me_flow(self):
        login_res = client.post("/auth/dev-login")
        self.assertEqual(login_res.status_code, 200)
        login_data = login_res.json()
        self.assertTrue(login_data["success"])
        self.assertEqual(login_data["user"]["email"], "demo-engineer@resumeiq.local")

        # Test auth/me with cookies preserved
        me_res = client.get("/auth/me")
        self.assertEqual(me_res.status_code, 200)
        me_data = me_res.json()
        self.assertIsNotNone(me_data["user"])
        self.assertEqual(me_data["user"]["email"], "demo-engineer@resumeiq.local")

        # Test auth/logout
        logout_res = client.post("/auth/logout")
        self.assertEqual(logout_res.status_code, 200)

        # After logout, me returns user = None
        me_after = client.get("/auth/me")
        self.assertIsNone(me_after.json()["user"])

    def test_analyze_rejects_gibberish_with_400(self):
        files = {"file": ("bad_resume.txt", io.BytesIO(b"asdfghjkl zxcvbnm qwertyuiop asdfghjk"), "text/plain")}
        res = client.post("/api/analyze", files=files, data={"jd": "", "top_n": 5})
        self.assertEqual(res.status_code, 400)
        detail = res.json().get("detail", {})
        self.assertFalse(detail.get("valid_resume"))
        self.assertIn("message", detail)

    def test_analyze_accepts_valid_sample_resume(self):
        files = {"file": ("sample_resume.txt", io.BytesIO(self.sample_resume.encode("utf-8")), "text/plain")}
        res = client.post("/api/analyze", files=files, data={"jd": "Backend Engineer", "top_n": 5})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["valid_resume"])
        self.assertIn("ats", data)
        self.assertIn("matches", data)
        self.assertIn("study_links", data)
        self.assertIn("predicted_roles", data)
        self.assertLessEqual(len(data["predicted_roles"]), 5)

    def test_predict_roles_top_10(self):
        res = client.post("/api/roles/predict", data={"resume_text": self.sample_resume, "top_n": 10})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("predicted_roles", data)
        self.assertLessEqual(len(data["predicted_roles"]), 10)

    def test_study_links_api(self):
        res = client.post("/api/skills/study", data={"skills": "Docker, Kubernetes, Redis"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["study_links"]), 3)
        self.assertTrue(all("url" in item for item in data["study_links"]))


if __name__ == "__main__":
    unittest.main()

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
            "Once upon a time in 2024, there was an engineer with great leadership skills and experience. "
            "He loved nature and walked near the trees every day in the forest with his friends and family."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(story)
        self.assertFalse(is_valid)
        self.assertTrue("not appear to be a resume" in err_msg.lower() or "detected" in err_msg.lower() or "story" in err_msg.lower())

    def test_non_resume_document_types_rejected(self):
        recipe = (
            "Chocolate Chip Cookies Recipe 2024. Ingredients: 2 cups flour, 1 cup butter, chocolate chips. "
            "Experience the best cookies in town with our management approved recipe. Contact chef@baking.com."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(recipe)
        self.assertFalse(is_valid)
        self.assertIn("recipe", err_msg.lower())

        terms = (
            "Terms of Service. Last updated 2024. By using our service you agree to these terms and conditions. "
            "Management reserves all rights. For communication contact support@service.com."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(terms)
        self.assertFalse(is_valid)
        self.assertIn("legal agreement", err_msg.lower())

        invoice = (
            "Invoice #1024. Date: 2024-01-15. Billed to: Acme Corp. Item: Software consulting services. "
            "Payment due in 30 days. Contact accounting@vendor.com for billing support."
        )
        is_valid, err_msg, warnings, details = E.validate_resume_text(invoice)
        self.assertFalse(is_valid)
        self.assertIn("invoice", err_msg.lower())

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

    def test_custom_login_endpoint(self):
        login_res = client.post("/auth/custom-login", data={"name": "Ankit Kumar", "email": "ankit@example.com"})
        self.assertEqual(login_res.status_code, 200)
        data = login_res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["name"], "Ankit Kumar")
        self.assertEqual(data["user"]["email"], "ankit@example.com")

        # Verify auth/me preserves custom login
        me_res = client.get("/auth/me")
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["name"], "Ankit Kumar")

        client.post("/auth/logout")

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

    def test_validate_endpoint_detects_non_resume(self):
        non_resume_content = b"Recipe for pasta: 2 cups flour, 1 egg. Boil water and cook for 10 minutes. Enjoy with tomato sauce."
        files = {"file": ("recipe.txt", io.BytesIO(non_resume_content), "text/plain")}
        res = client.post("/api/validate", files=files)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["valid_resume"])
        self.assertIn("not", data["message"].lower())

    def test_validate_endpoint_accepts_valid_resume(self):
        files = {"file": ("sample_resume.txt", io.BytesIO(self.sample_resume.encode("utf-8")), "text/plain")}
        res = client.post("/api/validate", files=files)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["valid_resume"])

    def test_study_links_api(self):
        res = client.post("/api/skills/study", data={"skills": "Docker, Kubernetes, Redis"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["study_links"]), 3)
        self.assertTrue(all("url" in item for item in data["study_links"]))

    def test_skills_roadmap_and_portfolio_projects(self):
        res = client.post("/api/skills/roadmap", data={"skills": "Docker, Kubernetes, AWS, Redis"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("portfolio_projects", data)
        self.assertIn("official_docs", data)
        projects = data["portfolio_projects"]
        self.assertGreaterEqual(len(projects), 3)
        self.assertIn("resume_bullet", projects[0])
        self.assertIn("tech_stack", projects[0])
        self.assertIn("deliverables", projects[0])

        docs = data["official_docs"]
        self.assertGreaterEqual(len(docs), 3)
        self.assertIn("authority", docs[0])
        self.assertIn("url", docs[0])

    def test_skills_tracker_crud_endpoints(self):
        # 1. Post to track skill
        post_res = client.post("/api/skills/track", data={"skill": "Kubernetes", "status": "to_learn", "notes": "Target for Q1"})
        self.assertEqual(post_res.status_code, 200)
        self.assertTrue(post_res.json()["success"])

        # 2. Get tracked skills
        get_res = client.get("/api/skills/track")
        self.assertEqual(get_res.status_code, 200)
        tracked = get_res.json()["tracked_skills"]
        k8s_items = [item for item in tracked if item["skill"] == "Kubernetes"]
        self.assertGreater(len(k8s_items), 0)
        self.assertEqual(k8s_items[0]["status"], "to_learn")

        # 3. Update status to in_progress
        update_res = client.post("/api/skills/track", data={"skill": "Kubernetes", "status": "in_progress", "notes": "Building first cluster"})
        self.assertEqual(update_res.status_code, 200)

        get_res2 = client.get("/api/skills/track")
        updated_k8s = [item for item in get_res2.json()["tracked_skills"] if item["skill"] == "Kubernetes"][0]
        self.assertEqual(updated_k8s["status"], "in_progress")

        # 4. Delete tracked skill
        del_res = client.request("DELETE", "/api/skills/track", data={"skill": "Kubernetes"})
        self.assertEqual(del_res.status_code, 200)

    def test_salary_estimate_endpoint(self):
        res = client.post("/api/salary/estimate", data={"role_title": "Senior Backend Engineer", "years": "5.0", "skills": "Python, Go, AWS, Docker"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("display_range", data)
        self.assertIn("market_median", data)
        self.assertIn("tier", data)
        self.assertGreater(data["market_median"], 100000)

    def test_crm_kanban_pipeline_crud_flow(self):
        # 1. Create a job opportunity in Saved stage
        post_res = client.post("/api/crm/jobs", data={
            "role_title": "Staff Backend Engineer",
            "company": "Stripe",
            "location": "Remote - US",
            "salary_range": "$160k - $210k / yr",
            "match_score": "88",
            "stage": "saved",
            "url": "https://stripe.com/jobs/123",
            "notes": "Referred by Alex"
        })
        self.assertEqual(post_res.status_code, 200)
        job = post_res.json()["job"]
        job_id = job["id"]
        self.assertEqual(job["role_title"], "Staff Backend Engineer")
        self.assertEqual(job["stage"], "saved")

        # 2. Get CRM jobs and verify stages organization
        get_res = client.get("/api/crm/jobs")
        self.assertEqual(get_res.status_code, 200)
        crm_data = get_res.json()
        self.assertIn("stages", crm_data)
        saved_jobs = [j for j in crm_data["stages"]["saved"] if j["id"] == job_id]
        self.assertEqual(len(saved_jobs), 1)

        # 3. Update stage to Interviewing
        patch_res = client.patch(f"/api/crm/jobs/{job_id}", data={"stage": "interviewing", "notes": "System design round scheduled"})
        self.assertEqual(patch_res.status_code, 200)
        updated_job = patch_res.json()["job"]
        self.assertEqual(updated_job["stage"], "interviewing")

        # 4. Delete the job from pipeline
        del_res = client.delete(f"/api/crm/jobs/{job_id}")
        self.assertEqual(del_res.status_code, 200)

        # 5. Verify deletion
        get_res2 = client.get("/api/crm/jobs")
        remaining = [j for j in get_res2.json()["jobs"] if j["id"] == job_id]
        self.assertEqual(len(remaining), 0)


if __name__ == "__main__":
    unittest.main()

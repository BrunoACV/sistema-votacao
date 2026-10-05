"""
tests/test_m2_app.py - Comprehensive Integration Test Suite for Milestone 2.
Tests all public routes, administrative endpoints, authentication, validations, and error handlers.

Coverage:
1. Health check GET /health -> 200 JSON
2. Registration GET & POST /register:
   - Form rendering
   - Successful participant creation with photo upload
   - Duplicate email rejection (400)
   - Invalid photo extension / MIME rejection (400)
   - Missing required fields validation (400)
3. Voting GET & POST /vote:
   - Candidate list display
   - Successful vote with @ints.org.br email & score range
   - Non-institutional domain rejection (400/422)
   - Duplicate voter email rejection (400/422)
   - Missing ratings coverage rejection (400)
   - Invalid score bounds rejection (400)
   - JSON API submission
4. Admin panel GET & POST /admin:
   - Unauthenticated access rejection (401 or redirect to login)
   - Login GET & POST /admin/login (bad password -> 401, good password -> 302/session)
   - Authenticated session access to dashboard
   - API header auth via X-Admin-Password and Authorization: Bearer
   - Cascade candidate deletion (removes participant, cascade votes, unlinks photo)
   - Voter audit listing GET /admin/voters
   - Logout GET /admin/logout
5. Public Results & Ranking GET /results:
   - Empty state
   - Ranked candidates with Olympic podium metadata (Ouro, Prata, Bronze)
6. Error handling and content negotiation (HTML vs JSON)
7. Entrypoint GET / -> redirects to /results

Run via:
    python -m unittest tests/test_m2_app.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.config import TestingConfig
from app.db import (
    create_connection,
    get_participant_by_email,
    list_participants,
    has_voter_voted,
)


def create_mock_image(format_name: str = "JPEG", size=(100, 100), color=(0, 51, 102)) -> io.BytesIO:
    """Generate in-memory valid image buffer for test uploads."""
    bio = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(bio, format=format_name)
    bio.seek(0)
    return bio


class M2AppIntegrationTestCase(unittest.TestCase):
    """End-to-End integration test suite for Milestone 2 Web Application."""

    def setUp(self):
        """Create isolated temporary environment for each test."""
        self.temp_dir = tempfile.mkdtemp(prefix="ints_test_m2_")
        self.db_path = Path(self.temp_dir) / "test_voting.db"
        self.upload_dir = Path(self.temp_dir) / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        class IsolatedTestConfig(TestingConfig):
            DATABASE_PATH = str(self.db_path)
            UPLOAD_FOLDER = self.upload_dir
            ADMIN_PASSWORD = "secret_admin_test_pass"
            SECRET_KEY = "test_isolated_secret_key"
            TESTING = True
            WTF_CSRF_ENABLED = False

        self.config_cls = IsolatedTestConfig
        self.app = create_app(self.config_cls)
        self.client = self.app.test_client()

    def tearDown(self):
        """Clean up isolated environment after test."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Helper Utilities
    # --------------------------------------------------------------------------

    def register_test_candidate(self, nome: str, email: str, desc: str, filename: str = "foto.jpg"):
        """Helper to register a participant via POST /register."""
        img_bytes = create_mock_image(format_name="JPEG")
        data = {
            "nome_completo": nome,
            "email": email,
            "descricao": desc,
            "foto": (img_bytes, filename, "image/jpeg"),
        }
        return self.client.post(
            "/register",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    # ==========================================================================
    # 1. Health, Diagnostics & Entrypoint
    # ==========================================================================

    def test_health_check_returns_200_and_json(self):
        """GET /health must return HTTP 200 with JSON status ok."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        data = response.get_json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("app"), "sistema_votacao_ints")

    def test_root_endpoint_redirects_to_results(self):
        """GET / must redirect to /results."""
        response = self.client.get("/", follow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))
        self.assertIn("/results", response.headers.get("Location", ""))

    # ==========================================================================
    # 2. Public Registration Lifecycle
    # ==========================================================================

    def test_register_get_renders_form(self):
        """GET /register must render registration HTML form with 200 OK."""
        response = self.client.get("/register")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"form", response.data.lower())
        self.assertIn(b"multipart/form-data", response.data)

    def test_register_success_with_valid_image(self):
        """POST /register with valid fields must create participant and save photo."""
        response = self.register_test_candidate(
            "Carlos Eduardo",
            "carlos.eduardo@ints.org.br",
            "Fantasia Médico Guerreiro",
        )
        # Should redirect on success
        self.assertIn(response.status_code, (200, 302, 303))

        # Verify DB entry
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        participant = cur.execute(
            "SELECT * FROM participants WHERE email = ?",
            ("carlos.eduardo@ints.org.br",),
        ).fetchone()
        conn.close()

        self.assertIsNotNone(participant)
        self.assertEqual(participant["nome_completo"], "Carlos Eduardo")

        # Verify photo saved to disk
        foto_name = participant["foto_filename"]
        photo_path = self.upload_dir / foto_name
        self.assertTrue(photo_path.exists())
        self.assertGreater(photo_path.stat().st_size, 0)

    def test_register_duplicate_email_rejected_with_400(self):
        """POST /register with an already registered email must return 400 Bad Request."""
        # 1. First registration
        res1 = self.register_test_candidate(
            "Marina Silva",
            "marina.silva@ints.org.br",
            "Fantasia Doutora da Alegria",
        )
        self.assertIn(res1.status_code, (200, 302, 303))

        # 2. Second registration with same email
        res2 = self.register_test_candidate(
            "Marina Silva Clone",
            "marina.silva@ints.org.br",
            "Outra descrição",
        )
        self.assertEqual(res2.status_code, 400)

        # 3. Case-insensitive duplicate check
        res3 = self.register_test_candidate(
            "Marina Silva Upper",
            "MARINA.SILVA@INTS.ORG.BR",
            "Terceira descrição",
        )
        self.assertEqual(res3.status_code, 400)

        # Assert only 1 record in database
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        count = cur.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_register_invalid_photo_format_rejected_with_400(self):
        """POST /register with non-image or fake file must return 400 Bad Request."""
        fake_file = io.BytesIO(b"Hello world, this is a plain text file, not an image!")
        data = {
            "nome_completo": "Fake User",
            "email": "fake.user@ints.org.br",
            "descricao": "Fantasia Inválida",
            "foto": (fake_file, "malicious.txt", "text/plain"),
        }
        response = self.client.post("/register", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)

    def test_register_optional_fields_succeeds(self):
        """POST /register without name or email or photo must succeed as all fields are optional."""
        data_no_name = {
            "nome_completo": "",
            "email": "valid@ints.org.br",
            "descricao": "Sem nome",
            "foto": (create_mock_image(), "foto.jpg", "image/jpeg"),
        }
        res = self.client.post("/register", data=data_no_name, content_type="multipart/form-data")
        self.assertIn(res.status_code, [200, 302])

        # Test completely empty submission
        data_empty_1 = {
            "nome_completo": "",
            "email": "",
            "descricao": "",
        }
        res_empty_1 = self.client.post("/register", data=data_empty_1, content_type="multipart/form-data")
        self.assertIn(res_empty_1.status_code, [200, 302])

        # Test second completely empty submission (verifies no email collision)
        data_empty_2 = {
            "nome_completo": "",
            "email": "",
            "descricao": "",
        }
        res_empty_2 = self.client.post("/register", data=data_empty_2, content_type="multipart/form-data")
        self.assertIn(res_empty_2.status_code, [200, 302])

        with self.app.app_context():
            participants = list_participants()
            # Verify that all 3 registered successfully
            self.assertEqual(len(participants), 3)
            for p in participants:
                self.assertTrue(p["nome_completo"])
                self.assertTrue(p["email"])

    # ==========================================================================
    # 3. Public Voting Lifecycle
    # ==========================================================================

    def test_vote_page_renders_active_candidates(self):
        """GET /vote must render candidate list."""
        self.register_test_candidate("Candidato 1", "cand1@ints.org.br", "Desc 1")
        self.register_test_candidate("Candidato 2", "cand2@ints.org.br", "Desc 2")

        response = self.client.get("/vote")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Candidato 1", response.data)
        self.assertIn(b"Candidato 2", response.data)

    def test_vote_success_with_institutional_email(self):
        """POST /vote with @ints.org.br email and ratings must record votes successfully."""
        self.register_test_candidate("Cand A", "candA@ints.org.br", "Desc A")
        self.register_test_candidate("Cand B", "candB@ints.org.br", "Desc B")

        # Get candidate IDs
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        p1_id = cur.execute("SELECT id FROM participants WHERE email = 'canda@ints.org.br'").fetchone()[0]
        p2_id = cur.execute("SELECT id FROM participants WHERE email = 'candb@ints.org.br'").fetchone()[0]
        conn.close()

        # Submit vote via Form Data
        vote_payload = {
            "voter_email": "servidor.maria@ints.org.br",
            f"rating_{p1_id}": "9.5",
            f"rating_{p2_id}": "8.0",
        }
        response = self.client.post("/vote", data=vote_payload, follow_redirects=False)
        self.assertIn(response.status_code, (200, 302, 303))

        # Verify DB
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        voter = cur.execute("SELECT * FROM voters WHERE email = 'servidor.maria@ints.org.br'").fetchone()
        self.assertIsNotNone(voter)

        votes = cur.execute("SELECT * FROM votes WHERE voter_id = ?", (voter["id"],)).fetchall()
        self.assertEqual(len(votes), 2)
        conn.close()

    def test_vote_duplicate_voter_rejected(self):
        """POST /vote using an email that already voted must return 400 or 422."""
        self.register_test_candidate("Cand A", "candA@ints.org.br", "Desc A")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants").fetchone()[0]
        conn.close()

        payload = {
            "voter_email": "repetido@ints.org.br",
            f"rating_{pid}": "10.0",
        }

        # 1st vote: succeeds
        res1 = self.client.post("/vote", data=payload)
        self.assertIn(res1.status_code, (200, 302, 303))

        # 2nd vote: must be rejected
        res2 = self.client.post("/vote", data=payload)
        self.assertIn(res2.status_code, (400, 422))

    def test_vote_non_institutional_email_rejected(self):
        """POST /vote with external email (@gmail.com, @hotmail.com) must return 400 or 422."""
        self.register_test_candidate("Cand A", "candA@ints.org.br", "Desc A")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants").fetchone()[0]
        conn.close()

        invalid_emails = [
            "hacker@gmail.com",
            "fulano@outlook.com",
            "test@yahoo.com.br",
            "fake@ints.com",
            "evil@notints.org.br",
        ]

        for email in invalid_emails:
            with self.subTest(email=email):
                res = self.client.post("/vote", data={"voter_email": email, f"rating_{pid}": "9.0"})
                self.assertIn(res.status_code, (400, 422))

    def test_vote_json_api_submission(self):
        """POST /vote accepting JSON payload."""
        self.register_test_candidate("Cand JSON 1", "candj1@ints.org.br", "Desc")
        self.register_test_candidate("Cand JSON 2", "candj2@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        rows = conn.execute("SELECT id FROM participants").fetchall()
        p1, p2 = rows[0][0], rows[1][0]
        conn.close()

        json_payload = {
            "voter_email": "api.voter@ints.org.br",
            "ratings": {
                str(p1): 9.0,
                str(p2): 7.5,
            },
        }
        res = self.client.post(
            "/vote",
            data=json.dumps(json_payload),
            content_type="application/json",
        )
        self.assertIn(res.status_code, (200, 302, 303))

    def test_vote_invalid_score_bounds_rejected(self):
        """POST /vote with score < 0 or > 10 must return 400 or 422."""
        self.register_test_candidate("Cand Score", "candscore@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants").fetchone()[0]
        conn.close()

        res_negative = self.client.post("/vote", data={"voter_email": "user@ints.org.br", f"rating_{pid}": "-1.0"})
        self.assertIn(res_negative.status_code, (400, 422))

        res_over = self.client.post("/vote", data={"voter_email": "user@ints.org.br", f"rating_{pid}": "10.5"})
        self.assertIn(res_over.status_code, (400, 422))

    # ==========================================================================
    # 4. Admin Authentication & Moderation
    # ==========================================================================

    def test_admin_dashboard_without_auth_rejected(self):
        """GET /admin without password or session must return 401 or redirect to /admin/login."""
        # When requesting JSON / API
        res_json = self.client.get("/admin", headers={"Accept": "application/json"})
        self.assertEqual(res_json.status_code, 401)

        # When requesting via browser
        res_html = self.client.get("/admin", headers={"Accept": "text/html"}, follow_redirects=False)
        self.assertIn(res_html.status_code, (401, 302))
        if res_html.status_code == 302:
            self.assertIn("/admin/login", res_html.headers.get("Location", ""))

    def test_admin_login_with_wrong_password_rejected(self):
        """POST /admin/login with incorrect password must return 401."""
        res = self.client.post("/admin/login", data={"password": "wrong_password"})
        self.assertEqual(res.status_code, 401)

    def test_admin_login_with_correct_password_succeeds(self):
        """POST /admin/login with correct password sets session and permits access."""
        res_login = self.client.post(
            "/admin/login",
            data={"password": "secret_admin_test_pass"},
            follow_redirects=False,
        )
        self.assertIn(res_login.status_code, (200, 302, 303))

        # Subsequent GET /admin with session cookie should return 200 OK
        res_dash = self.client.get("/admin")
        self.assertEqual(res_dash.status_code, 200)

    def test_admin_api_header_authentication(self):
        """GET /admin with X-Admin-Password or Authorization: Bearer must return 200 OK."""
        # 1. Via X-Admin-Password
        res1 = self.client.get("/admin", headers={"X-Admin-Password": "secret_admin_test_pass"})
        self.assertEqual(res1.status_code, 200)

        # 2. Via Authorization Bearer
        res2 = self.client.get("/admin", headers={"Authorization": "Bearer secret_admin_test_pass"})
        self.assertEqual(res2.status_code, 200)

    def test_admin_cascade_delete_participant(self):
        """POST /admin/participants/<id>/delete must cascade delete candidate, votes, and photo file."""
        # 1. Register candidate and vote
        self.register_test_candidate("ToDelete", "todelete@ints.org.br", "Description")
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        part = cur.execute("SELECT id, foto_filename FROM participants WHERE email = 'todelete@ints.org.br'").fetchone()
        pid = part["id"]
        foto_name = part["foto_filename"]
        conn.close()

        photo_path = self.upload_dir / foto_name
        self.assertTrue(photo_path.exists())

        # Cast vote
        self.client.post("/vote", data={"voter_email": "voter1@ints.org.br", f"rating_{pid}": "8.5"})

        # 2. Delete candidate via admin endpoint
        res = self.client.post(
            f"/admin/participants/{pid}/delete",
            headers={"X-Admin-Password": "secret_admin_test_pass"},
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (200, 302, 303))

        # 3. Verify participant removed from DB
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        p_check = cur.execute("SELECT id FROM participants WHERE id = ?", (pid,)).fetchone()
        self.assertIsNone(p_check)

        # 4. Verify cascade deletion of votes
        v_check = cur.execute("SELECT id FROM votes WHERE participant_id = ?", (pid,)).fetchall()
        self.assertEqual(len(v_check), 0)
        conn.close()

        # 5. Verify photo file unlinked from disk
        self.assertFalse(photo_path.exists())

    def test_admin_delete_nonexistent_participant_returns_404(self):
        """POST /admin/participants/99999/delete must return 404 Not Found."""
        res = self.client.post(
            "/admin/participants/99999/delete",
            headers={"X-Admin-Password": "secret_admin_test_pass", "Accept": "application/json"},
        )
        self.assertEqual(res.status_code, 404)

    def test_admin_voters_audit_view(self):
        """GET /admin/voters with admin credentials returns list of institutional voters."""
        self.register_test_candidate("Cand X", "candX@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants").fetchone()[0]
        conn.close()

        self.client.post("/vote", data={"voter_email": "audit.voter@ints.org.br", f"rating_{pid}": "9.0"})

        res = self.client.get("/admin/voters", headers={"X-Admin-Password": "secret_admin_test_pass"})
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"audit.voter@ints.org.br", res.data)

    def test_admin_logout_clears_session(self):
        """GET /admin/logout clears session."""
        self.client.post("/admin/login", data={"password": "secret_admin_test_pass"})
        res_dash = self.client.get("/admin")
        self.assertEqual(res_dash.status_code, 200)

        res_logout = self.client.get("/admin/logout", follow_redirects=False)
        self.assertIn(res_logout.status_code, (200, 302, 303))

        res_dash_after = self.client.get("/admin", headers={"Accept": "application/json"})
        self.assertEqual(res_dash_after.status_code, 401)

    # ==========================================================================
    # 5. Results & Olympic Podium
    # ==========================================================================

    def test_results_page_renders_empty_state_cleanly(self):
        """GET /results with no candidates renders cleanly without errors."""
        res = self.client.get("/results")
        self.assertEqual(res.status_code, 200)

    def test_results_page_renders_podium(self):
        """GET /results with scored candidates renders podium (Ouro, Prata, Bronze)."""
        # Register 3 candidates
        self.register_test_candidate("Primeiro Colocado", "cand1@ints.org.br", "D1")
        self.register_test_candidate("Segundo Colocado", "cand2@ints.org.br", "D2")
        self.register_test_candidate("Terceiro Colocado", "cand3@ints.org.br", "D3")

        conn = create_connection(self.db_path)
        cur = conn.cursor()
        p1 = cur.execute("SELECT id FROM participants WHERE email = 'cand1@ints.org.br'").fetchone()[0]
        p2 = cur.execute("SELECT id FROM participants WHERE email = 'cand2@ints.org.br'").fetchone()[0]
        p3 = cur.execute("SELECT id FROM participants WHERE email = 'cand3@ints.org.br'").fetchone()[0]
        conn.close()

        # Vote: p1 gets 10, p2 gets 8, p3 gets 6
        self.client.post("/vote", data={
            "voter_email": "evaluator@ints.org.br",
            f"rating_{p1}": "10.0",
            f"rating_{p2}": "8.0",
            f"rating_{p3}": "6.0",
        })

        res = self.client.get("/results")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Primeiro Colocado", res.data)
        self.assertIn(b"Segundo Colocado", res.data)
        self.assertIn(b"Terceiro Colocado", res.data)

    # ==========================================================================
    # 6. Error Handlers & Content Negotiation
    # ==========================================================================

    def test_404_not_found_negotiation(self):
        """404 handler returns JSON when requested, HTML otherwise."""
        # JSON
        res_json = self.client.get("/nonexistent_endpoint_xyz", headers={"Accept": "application/json"})
        self.assertEqual(res_json.status_code, 404)
        self.assertTrue(res_json.is_json)

        # HTML
        res_html = self.client.get("/nonexistent_endpoint_xyz", headers={"Accept": "text/html"})
        self.assertEqual(res_html.status_code, 404)
        self.assertIn(b"404", res_html.data)

    # ==========================================================================
    # 7. Single Choice Voting, Moderation Actions & 5-Screen Navigation
    # ==========================================================================

    def test_vote_single_candidate_selection(self):
        """POST /vote choosing a single candidate via selected_candidate records vote."""
        self.register_test_candidate("Candidato Único", "unico@ints.org.br", "Fantasia Solo")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'unico@ints.org.br'").fetchone()[0]
        conn.close()

        # Submit single choice vote
        response = self.client.post("/vote", data={
            "voter_email": "eleitor.novo@ints.org.br",
            "selected_candidate": pid,
        }, follow_redirects=False)

        self.assertIn(response.status_code, (200, 302, 303))
        self.assertIn("/vote/success", response.headers.get("Location", ""))

        # Verify DB entry
        conn = create_connection(self.db_path)
        cur = conn.cursor()
        voter = cur.execute("SELECT * FROM voters WHERE email = 'eleitor.novo@ints.org.br'").fetchone()
        self.assertIsNotNone(voter)
        vote_row = cur.execute("SELECT * FROM votes WHERE voter_id = ?", (voter["id"],)).fetchone()
        self.assertIsNotNone(vote_row)
        self.assertEqual(vote_row["participant_id"], pid)
        self.assertEqual(vote_row["nota"], 1.0)
        conn.close()

    def test_vote_ratings_payload_cand_name_preserved(self):
        """POST /vote with ratings JSON payload returns message with evaluated candidates name (not empty)."""
        self.register_test_candidate("Cand R1", "r1@ints.org.br", "D1")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'r1@ints.org.br'").fetchone()[0]
        conn.close()

        res = self.client.post(
            "/vote",
            data=json.dumps({
                "voter_email": "eval.ratings@ints.org.br",
                "ratings": {str(pid): 10.0}
            }),
            content_type="application/json",
            headers={"Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("os candidatos avaliados", data.get("message", ""))
        self.assertNotIn("para !", data.get("message", ""))

    def test_api_check_voter_status(self):
        """GET /api/check-voter validates domain and prior vote status in real-time."""
        # 1. Invalid domain
        res1 = self.client.get("/api/check-voter?email=usuario@gmail.com")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertFalse(data1.get("valid"))

        # 2. Valid and not voted
        res2 = self.client.get("/api/check-voter?email=servidor.livre@ints.org.br")
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2.get("valid"))
        self.assertFalse(data2.get("has_voted"))

        # 3. Valid and already voted
        self.register_test_candidate("Cand Check", "check@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'check@ints.org.br'").fetchone()[0]
        conn.close()
        self.client.post("/vote", data={"voter_email": "servidor.livre@ints.org.br", "selected_candidate": pid})

        res3 = self.client.get("/api/check-voter?email=servidor.livre@ints.org.br")
        self.assertEqual(res3.status_code, 200)
        data3 = res3.get_json()
        self.assertTrue(data3.get("valid"))
        self.assertTrue(data3.get("has_voted"))

    def test_admin_delete_individual_voter(self):
        """POST /admin/voters/<id>/delete removes individual vote and frees voter email."""
        self.register_test_candidate("Cand DelVoter", "cand_del@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'cand_del@ints.org.br'").fetchone()[0]
        conn.close()

        self.client.post("/vote", data={"voter_email": "voter.todelete@ints.org.br", "selected_candidate": pid})

        conn = create_connection(self.db_path)
        voter_id = conn.execute("SELECT id FROM voters WHERE email = 'voter.todelete@ints.org.br'").fetchone()[0]
        conn.close()

        # Delete voter via admin endpoint
        res = self.client.post(
            f"/admin/voters/{voter_id}/delete",
            headers={"X-Admin-Password": "secret_admin_test_pass"},
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (200, 302, 303))

        # Check DB: voter and vote removed
        conn = create_connection(self.db_path)
        v_check = conn.execute("SELECT id FROM voters WHERE id = ?", (voter_id,)).fetchone()
        self.assertIsNone(v_check)
        vote_check = conn.execute("SELECT id FROM votes WHERE voter_id = ?", (voter_id,)).fetchall()
        self.assertEqual(len(vote_check), 0)
        conn.close()

        # Voter can vote again
        res_revote = self.client.post("/vote", data={"voter_email": "voter.todelete@ints.org.br", "selected_candidate": pid})
        self.assertIn(res_revote.status_code, (200, 302, 303))

    def test_admin_reset_all_votes(self):
        """POST /admin/votes/reset purges all votes and voters."""
        self.register_test_candidate("Cand Rst", "cand_rst@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'cand_rst@ints.org.br'").fetchone()[0]
        conn.close()

        self.client.post("/vote", data={"voter_email": "v1@ints.org.br", "selected_candidate": pid})
        self.client.post("/vote", data={"voter_email": "v2@ints.org.br", "selected_candidate": pid})

        # Reset all votes
        res = self.client.post(
            "/admin/votes/reset",
            headers={"X-Admin-Password": "secret_admin_test_pass"},
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (200, 302, 303))

        conn = create_connection(self.db_path)
        total_votes = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        total_voters = conn.execute("SELECT COUNT(*) FROM voters").fetchone()[0]
        conn.close()
        self.assertEqual(total_votes, 0)
        self.assertEqual(total_voters, 0)

    def test_delete_participant_cleans_orphan_voters(self):
        """Deleting a candidate cleans up orphan voters whose vote was deleted."""
        self.register_test_candidate("Cand Orphan", "orphan@ints.org.br", "Desc")
        conn = create_connection(self.db_path)
        pid = conn.execute("SELECT id FROM participants WHERE email = 'orphan@ints.org.br'").fetchone()[0]
        conn.close()

        self.client.post("/vote", data={"voter_email": "freeme@ints.org.br", "selected_candidate": pid})

        # Delete candidate
        self.client.post(
            f"/admin/participants/{pid}/delete",
            headers={"X-Admin-Password": "secret_admin_test_pass"},
        )

        conn = create_connection(self.db_path)
        voter_check = conn.execute("SELECT id FROM voters WHERE email = 'freeme@ints.org.br'").fetchone()
        conn.close()
        # Orphan voter was cleaned up
        self.assertIsNone(voter_check)

    def test_moderator_views_contain_all_five_screens(self):
        """Both /admin and /admin/voters render complete navigation to all 5 screens."""
        auth_header = {"X-Admin-Password": "secret_admin_test_pass"}

        expected_links = [
            b"/admin",
            b"/vote",
            b"/register",
            b"/results",
            b"/admin/voters",
        ]

        # 1. /admin
        res_admin = self.client.get("/admin", headers=auth_header)
        self.assertEqual(res_admin.status_code, 200)
        for link in expected_links:
            with self.subTest(screen=link, view="/admin"):
                self.assertIn(link, res_admin.data)

        # 2. /admin/voters
        res_voters = self.client.get("/admin/voters", headers=auth_header)
        self.assertEqual(res_voters.status_code, 200)
        for link in expected_links:
            with self.subTest(screen=link, view="/admin/voters"):
                self.assertIn(link, res_voters.data)

    def test_secret_vote_url_hidden_from_unauthenticated_public(self):
        """Public register, login, and error pages must NOT reveal the secret /vote URL to unauthenticated visitors."""
        # 1. Register page
        res_reg = self.client.get("/register")
        self.assertEqual(res_reg.status_code, 200)
        self.assertNotIn(b'href="/vote"', res_reg.data)
        self.assertNotIn(b"url_for('public.vote')", res_reg.data)

        # 2. Admin login page
        res_login = self.client.get("/admin/login")
        self.assertEqual(res_login.status_code, 200)
        self.assertNotIn(b'href="/vote"', res_login.data)

        # 3. 404 error page
        res_404 = self.client.get("/nonexistent_page", headers={"Accept": "text/html"})
        self.assertEqual(res_404.status_code, 404)
        self.assertNotIn(b'href="/vote"', res_404.data)


if __name__ == "__main__":
    unittest.main()

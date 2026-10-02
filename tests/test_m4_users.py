# -*- coding: utf-8 -*-
"""
tests/test_m4_users.py
Comprehensive test suite for institutional administrator users, authentication,
passwords, and first-access mandatory password reset in INTS Voting System.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import TestingConfig
import app.db as db


class UsersAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ints_test_users_")
        self.db_path = Path(self.temp_dir) / "test_users.db"
        self.upload_dir = Path(self.temp_dir) / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        class UsersTestConfig(TestingConfig):
            DATABASE_PATH = str(self.db_path)
            UPLOAD_FOLDER = str(self.upload_dir)
            ADMIN_PASSWORD = "admin_master_pwd"
            SECRET_KEY = "test-users-secret-key"
            TESTING = True
            WTF_CSRF_ENABLED = False

        self.app = create_app(UsersTestConfig)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_seeded_users(self):
        """Verifies that bruno is automatically seeded as admin on database initialization and amanda does not exist."""
        with self.app.app_context():
            bruno = db.get_user_by_username("bruno")
            self.assertIsNotNone(bruno)
            self.assertEqual(bruno["username"], "bruno")
            self.assertEqual(bruno["is_admin"], 1)
            self.assertEqual(bruno["must_change_password"], 1)

            # Amanda must NOT exist
            amanda = db.get_user_by_username("amanda")
            self.assertIsNone(amanda)

            # Authenticate with initial password 'trocar'
            auth_bruno = db.authenticate_user("bruno", "trocar")
            self.assertIsNotNone(auth_bruno)
            self.assertEqual(auth_bruno["id"], bruno["id"])

            # Incorrect password should return None
            self.assertIsNone(db.authenticate_user("bruno", "wrong_password"))

    def test_first_access_mandatory_password_change_flow(self):
        """Tests that a user with must_change_password=1 cannot access admin pages until changing password."""
        # 1. Login with bruno / trocar
        resp = self.client.post("/admin/login", data={"username": "bruno", "password": "trocar"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/change-password", resp.headers["Location"])

        # 2. Follow redirect to change password
        resp_cp = self.client.get("/admin/change-password")
        self.assertEqual(resp_cp.status_code, 200)
        self.assertIn("Primeiro Acesso", resp_cp.get_data(as_text=True))
        self.assertIn("bruno", resp_cp.get_data(as_text=True))

        # 3. Attempting to bypass by navigating to dashboard directly should be redirected back
        resp_bypass = self.client.get("/admin/", follow_redirects=False)
        self.assertEqual(resp_bypass.status_code, 302)
        self.assertIn("/admin/change-password", resp_bypass.headers["Location"])

        # 4. Attempting to change to 'trocar' again must fail
        resp_fail_trocar = self.client.post("/admin/change-password", data={
            "new_password": "trocar",
            "confirm_password": "trocar"
        }, follow_redirects=True)
        self.assertIn("A nova senha não pode ser a senha padrão", resp_fail_trocar.get_data(as_text=True))

        # 5. Attempting short password (< 6 chars) must fail
        resp_fail_short = self.client.post("/admin/change-password", data={
            "new_password": "123",
            "confirm_password": "123"
        }, follow_redirects=True)
        self.assertIn("mínimo 6 caracteres", resp_fail_short.get_data(as_text=True))

        # 6. Non-matching passwords must fail
        resp_fail_mismatch = self.client.post("/admin/change-password", data={
            "new_password": "NovaSenhaSegura1",
            "confirm_password": "OutraSenhaSegura2"
        }, follow_redirects=True)
        self.assertIn("não confere", resp_fail_mismatch.get_data(as_text=True))

        # 7. Valid password change
        resp_success = self.client.post("/admin/change-password", data={
            "new_password": "MinhaNovaSenha@2026",
            "confirm_password": "MinhaNovaSenha@2026"
        }, follow_redirects=True)
        self.assertEqual(resp_success.status_code, 200)
        self.assertIn("Painel Geral", resp_success.get_data(as_text=True))

        # 8. Now direct access to admin dashboard works
        resp_dash = self.client.get("/admin/")
        self.assertEqual(resp_dash.status_code, 200)

        # 9. Verify database updated
        with self.app.app_context():
            updated_bruno = db.get_user_by_username("bruno")
            self.assertEqual(updated_bruno["must_change_password"], 0)
            self.assertIsNotNone(db.authenticate_user("bruno", "MinhaNovaSenha@2026"))
            self.assertIsNone(db.authenticate_user("bruno", "trocar"))

    def test_admin_user_creation_and_listing(self):
        """Tests adding a new admin user and rendering on the users list page."""
        # Login with master password
        self.client.post("/admin/login", data={"password": "admin_master_pwd"}, follow_redirects=True)

        # Visit users page
        resp = self.client.get("/admin/users")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Administradores do Sistema", body)
        self.assertIn("bruno", body)

        # Create new admin user 'roberto'
        resp_create = self.client.post("/admin/users/new", data={
            "username": "roberto",
            "nome": "Roberto Carlos",
            "password": "trocar"
        }, follow_redirects=True)
        self.assertEqual(resp_create.status_code, 200)
        self.assertIn("roberto", resp_create.get_data(as_text=True))

        # Verify in DB
        with self.app.app_context():
            u = db.get_user_by_username("roberto")
            self.assertIsNotNone(u)
            self.assertEqual(u["is_admin"], 1)
            self.assertEqual(u["must_change_password"], 1)
            self.assertEqual(u["nome"], "Roberto Carlos")

        # Duplicate username should be rejected
        resp_dup = self.client.post("/admin/users/new", data={
            "username": "roberto",
            "nome": "Outro Roberto",
            "password": "trocar"
        }, follow_redirects=True)
        self.assertIn("já está em uso", resp_dup.get_data(as_text=True))

    def test_admin_delete_user(self):
        """Tests deleting an admin user while preventing self-deletion."""
        with self.app.app_context():
            # Create a secondary admin user 'carlos'
            db.create_user("carlos", "trocar", "Carlos", is_admin=1, must_change_password=0)

        # Login as carlos
        self.client.post("/admin/login", data={"username": "carlos", "password": "trocar"})

        with self.app.app_context():
            bruno = db.get_user_by_username("bruno")
            carlos = db.get_user_by_username("carlos")

        # Carlos cannot delete himself
        resp_self = self.client.post(f"/admin/users/{carlos['id']}/delete", follow_redirects=True)
        self.assertIn("não pode excluir sua própria conta", resp_self.get_data(as_text=True))

        # Carlos can delete bruno
        resp_del = self.client.post(f"/admin/users/{bruno['id']}/delete", follow_redirects=True)
        self.assertIn("excluído com sucesso", resp_del.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.get_user_by_username("bruno"))

    def test_legacy_password_login_backward_compatibility(self):
        """Ensures that posting only password still works for backwards compatibility."""
        resp = self.client.post("/admin/login", data={"password": "admin_master_pwd"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Painel Geral", resp.get_data(as_text=True))

# -*- coding: utf-8 -*-
"""
Multi-event test suite for parallel voting system in INTS.
Verifies event creation, slugification, parallel voting isolation,
and moderation controls across multiple simultaneous events.
"""

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from app import create_app
from app.config import TestingConfig
import app.db as db


def create_dummy_png_bytes(width: int = 200, height: int = 200, color: str = "orange") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


class MultiEventTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ints_test_multi_")
        self.db_path = Path(self.temp_dir) / "test_multi.db"
        self.upload_dir = Path(self.temp_dir) / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        class MultiTestConfig(TestingConfig):
            DATABASE_PATH = str(self.db_path)
            UPLOAD_FOLDER = str(self.upload_dir)
            ADMIN_PASSWORD = "admin_test_pass"
            SECRET_KEY = "test-multi-secret-key"
            TESTING = True
            WTF_CSRF_ENABLED = False

        self.app = create_app(MultiTestConfig)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _login_admin(self):
        return self.client.post("/admin/login", data={"password": "admin_test_pass"}, follow_redirects=True)

    def test_database_multi_event_creation_and_slugification(self):
        """Tests programmatic creation of multiple events with auto-slug generation."""
        with self.app.app_context():
            ev1 = db.create_event(nome="Concurso de Halloween 2026", descricao="Festa das bruxas")
            ev2 = db.create_event(nome="Premio de Inovacao em Saude", descricao="Melhores projetos de tecnologia")

            self.assertIsNotNone(ev1)
            self.assertIsNotNone(ev2)
            self.assertEqual(ev1["slug"], "concurso-de-halloween-2026")
            self.assertEqual(ev2["slug"], "premio-de-inovacao-em-saude")

            # Duplicate name handling: appends unique suffix
            ev3 = db.create_event(nome="Concurso de Halloween 2026")
            self.assertTrue(ev3["slug"].startswith("concurso-de-halloween-2026-"))

    def test_parallel_voting_isolation_same_voter_different_events(self):
        """
        Critical test: Verifies that a collaborator with an @ints.org.br email
        can vote in Event 1 AND vote in Event 2 simultaneously without collision.
        Verifies that double-voting inside the same event is strictly blocked.
        """
        with self.app.app_context():
            # Create two separate events
            ev_fantasia = db.create_event(nome="Melhor Fantasia", descricao="Concurso de fantasias")
            ev_projeto = db.create_event(nome="Melhor Projeto TI", descricao="Concurso de projetos de TI")

            # Add participants to Event 1
            p1 = db.add_participant(
                nome_completo="Ana Fantasiada",
                email="ana@ints.org.br",
                descricao="Fantasia de Bruxa",
                foto_filename="foto1.png",
                event_id=ev_fantasia["id"]
            )
            # Add participants to Event 2
            p2 = db.add_participant(
                nome_completo="Carlos Programador",
                email="carlos@ints.org.br",
                descricao="Projeto Telemedicina",
                foto_filename="foto2.png",
                event_id=ev_projeto["id"]
            )

            voter_email = "bruno.costa@ints.org.br"

            # 1. Voter has not voted in either event
            self.assertFalse(db.has_voter_voted(voter_email, event_id=ev_fantasia["id"]))
            self.assertFalse(db.has_voter_voted(voter_email, event_id=ev_projeto["id"]))

            # 2. Voter votes in Event 1
            res1 = db.record_single_vote(voter_email, p1, event_id=ev_fantasia["id"])
            self.assertEqual(res1["event_id"], ev_fantasia["id"])

            # Voter is now marked as voted in Event 1, but STILL ELIGIBLE in Event 2!
            self.assertTrue(db.has_voter_voted(voter_email, event_id=ev_fantasia["id"]))
            self.assertFalse(db.has_voter_voted(voter_email, event_id=ev_projeto["id"]))

            # 3. Voter tries to vote again in Event 1 -> Must raise VoterAlreadyVotedError
            with self.assertRaises(db.VoterAlreadyVotedError):
                db.record_single_vote(voter_email, p1, event_id=ev_fantasia["id"])

            # 4. Voter votes in Event 2 -> Must succeed!
            res2 = db.record_single_vote(voter_email, p2, event_id=ev_projeto["id"])
            self.assertEqual(res2["event_id"], ev_projeto["id"])

            # Voter is now marked as voted in both events
            self.assertTrue(db.has_voter_voted(voter_email, event_id=ev_fantasia["id"]))
            self.assertTrue(db.has_voter_voted(voter_email, event_id=ev_projeto["id"]))

            # 5. Voter tries to vote again in Event 2 -> Must raise VoterAlreadyVotedError
            with self.assertRaises(db.VoterAlreadyVotedError):
                db.record_single_vote(voter_email, p2, event_id=ev_projeto["id"])

            # 6. Verify leaderboards are strictly isolated
            lb1 = db.get_leaderboard(event_id=ev_fantasia["id"])
            lb2 = db.get_leaderboard(event_id=ev_projeto["id"])

            self.assertEqual(len(lb1), 1)
            self.assertEqual(lb1[0]["id"], p1)
            self.assertEqual(lb1[0]["total_votos"], 1)

            self.assertEqual(len(lb2), 1)
            self.assertEqual(lb2[0]["id"], p2)
            self.assertEqual(lb2[0]["total_votos"], 1)

    def test_inactive_event_blocks_votes_and_registrations(self):
        """Tests that pausing an event blocks voting and registrations."""
        with self.app.app_context():
            ev = db.create_event(nome="Evento Encerrado", descricao="Votacao ja finalizada", ativo=0)

            # Attempting to add participant directly raises EventInactiveError
            with self.assertRaises(db.EventInactiveError):
                db.add_participant(
                    nome_completo="Tentativa Invalida",
                    email="teste@ints.org.br",
                    descricao="Descricao",
                    foto_filename="foto.png",
                    event_id=ev["id"]
                )

        # HTTP request to vote in inactive event
        resp = self.client.post(
            f"/e/{ev['slug']}/vote",
            data={"selected_candidate": "1", "voter_email": "usuario@ints.org.br"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_admin_create_event_and_toggle_status_via_http(self):
        """Tests admin HTTP endpoints for creating, toggling, and managing events."""
        login_resp = self._login_admin()
        self.assertEqual(login_resp.status_code, 200)

        # 1. Create a new event via /admin/events/new
        create_resp = self.client.post("/admin/events/new", data={
            "nome": "Concurso de Ideias 2026",
            "descricao": "Inovacao corporativa no INTS",
            "ativo": "1"
        }, follow_redirects=True)
        self.assertEqual(create_resp.status_code, 200)

        with self.app.app_context():
            ev = db.get_event_by_slug("concurso-de-ideias-2026")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["ativo"], 1)
            ev_id = ev["id"]

        # 2. Toggle status (pause event)
        toggle_resp = self.client.post(f"/admin/events/{ev_id}/toggle-status", follow_redirects=True)
        self.assertEqual(toggle_resp.status_code, 200)

        with self.app.app_context():
            ev_updated = db.get_event_by_id(ev_id)
            self.assertEqual(ev_updated["ativo"], 0)

        # 3. Delete event
        delete_resp = self.client.post(f"/admin/events/{ev_id}/delete", follow_redirects=True)
        self.assertEqual(delete_resp.status_code, 200)

        with self.app.app_context():
            ev_deleted = db.get_event_by_id(ev_id)
            self.assertIsNone(ev_deleted)

    def test_public_event_scoped_routes_and_catalog(self):
        """Tests that public routes render and resolve properly for /eventos and /e/<slug>/..."""
        with self.app.app_context():
            ev = db.create_event(nome="Festival da Primavera", descricao="Celebrando a primavera")
            slug = ev["slug"]

        # 1. Catalog route /eventos
        resp_catalog = self.client.get("/eventos")
        self.assertEqual(resp_catalog.status_code, 200)
        self.assertIn("Festival da Primavera".encode("utf-8"), resp_catalog.data)

        # 2. Event vote screen /e/<slug>/vote
        resp_vote = self.client.get(f"/e/{slug}/vote")
        self.assertEqual(resp_vote.status_code, 200)
        self.assertIn("Festival da Primavera".encode("utf-8"), resp_vote.data)

        # 3. Event register screen /e/<slug>/register
        resp_reg = self.client.get(f"/e/{slug}/register")
        self.assertEqual(resp_reg.status_code, 200)
        self.assertIn("Festival da Primavera".encode("utf-8"), resp_reg.data)

        # 4. Event results screen /e/<slug>/results
        resp_res = self.client.get(f"/e/{slug}/results")
        self.assertEqual(resp_res.status_code, 200)
        self.assertIn("Festival da Primavera".encode("utf-8"), resp_res.data)

    def test_event_theme_creation_and_carnaval_preset(self):
        """Tests that all 5 color themes (especially carnaval) can be selected and rendered."""
        with self.app.app_context():
            # 1. Carnaval event creation
            ev_carnaval = db.create_event(nome="Carnaval dos Colaboradores", tema="carnaval")
            self.assertEqual(ev_carnaval["tema"], "carnaval")

            # 2. Institucional event creation
            ev_inst = db.create_event(nome="Premio INTS Destaque", tema="institucional")
            self.assertEqual(ev_inst["tema"], "institucional")

            # 3. Sunset & Esmeralda event creation
            ev_sunset = db.create_event(nome="Concurso Fotografia Sunset", tema="sunset")
            self.assertEqual(ev_sunset["tema"], "sunset")
            ev_esmeralda = db.create_event(nome="Concurso Natureza Viva", tema="esmeralda")
            self.assertEqual(ev_esmeralda["tema"], "esmeralda")

            # 4. Invalid theme defaults to dracula
            ev_invalid = db.create_event(nome="Evento Tema Invalido", tema="inexistente")
            self.assertEqual(ev_invalid["tema"], "dracula")

        # 5. Check template rendering includes data-theme attribute
        resp = self.client.get(f"/e/{ev_carnaval['slug']}/vote")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'data-theme="carnaval"', resp.data)

    def test_admin_sidebar_and_public_navigation_isolation(self):
        """
        Tests that when unauthenticated, the user cannot navigate between other screens,
        and no results button is displayed.
        When logged in as admin, the left sidebar is present with full navigation.
        """
        # 1. Public user (unauthenticated) on voting page
        resp_pub = self.client.get("/vote")
        self.assertEqual(resp_pub.status_code, 200)
        # Verify no left sidebar for public
        self.assertNotIn(b'id="admin-sidebar"', resp_pub.data)
        # Verify no link to results
        self.assertNotIn(b'href="/results"', resp_pub.data)
        self.assertNotIn(b'href="/e/halloween/results"', resp_pub.data)

        # 2. Authenticated admin
        self._login_admin()
        resp_admin = self.client.get("/admin")
        self.assertEqual(resp_admin.status_code, 200)
        # Verify left sidebar navigation is rendered
        self.assertIn(b'<aside', resp_admin.data)
        self.assertIn(b'Modo Moderador', resp_admin.data)
        self.assertIn(b'Painel Geral', resp_admin.data)
        self.assertIn(b'Criar Novo Evento', resp_admin.data)


if __name__ == "__main__":
    unittest.main()

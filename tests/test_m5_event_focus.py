# -*- coding: utf-8 -*-
"""
O evento em foco acompanha a pessoa por todas as telas: abrir /eventos e voltar
para /vote, /results, /register ou /admin nao pode cair em outro evento.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import TestingConfig
import app.db as db

FOCO = "Carinhas Teste Foco"


class EventFocusTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ints_test_focus_")
        tmp = Path(self.temp_dir)
        (tmp / "uploads").mkdir()

        class FocusConfig(TestingConfig):
            DATABASE_PATH = str(tmp / "focus.db")
            UPLOAD_FOLDER = str(tmp / "uploads")
            ADMIN_PASSWORD = "admin_test_pass"
            SECRET_KEY = "test-focus-secret"
            TESTING = True
            WTF_CSRF_ENABLED = False

        self.app = create_app(FocusConfig)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.default = db.get_default_event()
            self.foco = db.create_event(nome=FOCO, descricao="evento em foco")
            self.outro = db.create_event(nome="Outro Evento Paralelo")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _event_shown(self, url):
        import re
        html = self.client.get(url, follow_redirects=True).get_data(as_text=True)
        title = (re.search(r"<title>(.*?)</title>", html, re.S) or [None, ""])[1]
        return [n for n in (FOCO, "Outro Evento Paralelo", self.default["nome"]) if n in title]

    def test_sem_foco_usa_evento_padrao(self):
        self.assertIn(self.default["nome"], self._event_shown("/vote"))
        self.assertNotIn(FOCO, self._event_shown("/vote"))

    def test_catalogo_e_volta_mantem_evento(self):
        self.client.get(f"/e/{self.foco['slug']}/vote")
        self.assertEqual(self.client.get("/eventos").status_code, 200)
        for url in ("/vote", "/results", "/register", "/vote", "/results"):
            self.client.get("/eventos")
            shown = self._event_shown(url)
            self.assertIn(FOCO, shown, url)
            self.assertNotIn(self.default["nome"], shown, url)

    def test_voto_sem_evento_na_url_vai_para_o_foco(self):
        self.client.get(f"/e/{self.foco['slug']}/results")
        r = self.client.post("/vote", json={"voter_email": "pessoa.teste@ints.org.br"},
                             headers={"Accept": "application/json"})
        msg = r.get_json()["message"]
        self.assertNotIn(self.default["nome"], msg)
        self.assertIn(FOCO, msg)  # evento sem participantes: mensagem cita o evento em foco

    def test_trocar_de_evento_troca_o_foco(self):
        self.client.get(f"/e/{self.foco['slug']}/vote")
        self.client.get(f"/vote?evento={self.outro['slug']}")
        self.assertIn("Outro Evento Paralelo", self._event_shown("/results"))
        self.assertNotIn(FOCO, self._event_shown("/results"))

    def test_slug_inexistente_nao_apaga_foco(self):
        self.client.get(f"/e/{self.foco['slug']}/vote")
        self.assertEqual(self.client.get("/e/nao-existe/vote").status_code, 404)
        self.assertIn(FOCO, self._event_shown("/vote"))

    def test_evento_removido_volta_ao_padrao(self):
        self.client.get(f"/e/{self.foco['slug']}/vote")
        with self.app.app_context():
            conn = db.get_db()
            conn.execute("DELETE FROM events WHERE id = ?", (self.foco["id"],))
            conn.commit()
        self.assertEqual(self.client.get("/vote").status_code, 200)
        self.assertIn(self.default["nome"], self._event_shown("/vote"))

    def test_moderador_sem_foco_ve_padrao(self):
        self.client.post("/admin/login", data={"password": "admin_test_pass"}, follow_redirects=True)
        html = self.client.get("/admin", follow_redirects=True).get_data(as_text=True)
        self.assertNotIn(f'title="{FOCO}"', html)

    def test_moderador_mantem_foco_no_painel(self):
        self.client.post("/admin/login", data={"password": "admin_test_pass"}, follow_redirects=True)
        self.client.get(f"/e/{self.foco['slug']}/vote")
        self.client.get("/eventos")
        for url in ("/admin", "/admin/voters", "/vote"):
            r = self.client.get(url, follow_redirects=True)
            html = r.get_data(as_text=True)
            self.assertIn(f'title="{FOCO}"', html, url)  # caixa "Evento Ativo" do menu lateral

    def test_indicador_de_evento_em_todas_as_telas_de_gestao(self):
        self.client.post("/admin/login", data={"password": "admin_test_pass"}, follow_redirects=True)
        self.client.get(f"/e/{self.foco['slug']}/vote")
        for url in ("/admin", "/admin/voters", "/admin/users", "/admin/events/new", "/eventos", "/admin/change-password"):
            r = self.client.get(url, follow_redirects=True)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(f'title="{FOCO}"', r.get_data(as_text=True), url)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
Historico de votos excluidos: toda exclusao (auditoria, zerar, candidato ou evento
removido, ou direto no banco) fica gravada com e-mail, candidato, evento e quem excluiu.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import TestingConfig
import app.db as db


class VoteDeletionsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ints_test_del_")
        tmp = Path(self.temp_dir)
        (tmp / "uploads").mkdir()

        class Cfg(TestingConfig):
            DATABASE_PATH = str(tmp / "del.db")
            UPLOAD_FOLDER = str(tmp / "uploads")
            ADMIN_PASSWORD = "admin_test_pass"
            SECRET_KEY = "test-del-secret"
            TESTING = True
            WTF_CSRF_ENABLED = False

        self.app = create_app(Cfg)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.ev = db.create_event(nome="Evento Exclusoes")
            self.ana = db.add_participant(nome_completo="Ana Candidata", event_id=self.ev["id"])
            self.bia = db.add_participant(nome_completo="Bia Candidata", event_id=self.ev["id"])
        self.client.post("/admin/login", data={"password": "admin_test_pass"}, follow_redirects=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _vote(self, email, cid):
        r = self.client.post(f"/e/{self.ev['slug']}/vote", json={"voter_email": email, "candidate_id": str(cid)},
                             headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def _voter_id(self, email):
        with self.app.app_context():
            return db.get_db().execute("SELECT id FROM voters WHERE email = ?", (email,)).fetchone()[0]

    def _rows(self):
        with self.app.app_context():
            return db.list_vote_deletions()

    def test_exclusao_pela_auditoria_guarda_tudo(self):
        self._vote("maria.teste@ints.org.br", self.ana)
        vid = self._voter_id("maria.teste@ints.org.br")
        self.client.post(f"/admin/voters/{vid}/delete")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["email"], "maria.teste@ints.org.br")
        self.assertEqual(r["candidatos"], "Ana Candidata")
        self.assertEqual(r["event_nome"], "Evento Exclusoes")
        self.assertEqual(r["origem"], "auditoria de eleitores")
        self.assertEqual(r["moderador"], "admin")
        self.assertTrue(r["voted_at"])

    def test_zerar_votos_guarda_cada_voto_uma_vez(self):
        self._vote("a.teste@ints.org.br", self.ana)
        self._vote("b.teste@ints.org.br", self.bia)
        self.client.post("/admin/votes/reset", data={"event_id": str(self.ev["id"])})
        rows = self._rows()
        self.assertEqual(sorted((r["email"], r["candidatos"]) for r in rows),
                         [("a.teste@ints.org.br", "Ana Candidata"), ("b.teste@ints.org.br", "Bia Candidata")])
        self.assertTrue(all(r["origem"] == "zerar votos" for r in rows))

    def test_candidato_removido_guarda_os_votos_dele(self):
        self._vote("c.teste@ints.org.br", self.ana)
        self._vote("d.teste@ints.org.br", self.bia)
        self.client.post(f"/admin/participants/{self.ana}/delete")
        rows = self._rows()
        self.assertEqual([(r["email"], r["candidatos"], r["origem"]) for r in rows],
                         [("c.teste@ints.org.br", "Ana Candidata", "candidato excluído")])

    def test_evento_removido_guarda_os_votos(self):
        self._vote("e.teste@ints.org.br", self.ana)
        self.client.post(f"/admin/events/{self.ev['id']}/delete")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["email"], rows[0]["candidatos"], rows[0]["event_nome"]),
                         ("e.teste@ints.org.br", "Ana Candidata", "Evento Exclusoes"))

    def test_exclusao_direto_no_banco_tambem_fica(self):
        self._vote("f.teste@ints.org.br", self.bia)
        with self.app.app_context():
            db.get_db().execute("DELETE FROM voters WHERE email = 'f.teste@ints.org.br'")
        rows = self._rows()
        self.assertEqual((rows[0]["email"], rows[0]["origem"], rows[0]["moderador"]),
                         ("f.teste@ints.org.br", "direto no banco", "-"))

    def test_historico_aparece_na_tela_de_auditoria(self):
        self._vote("g.teste@ints.org.br", self.ana)
        self.client.post(f"/admin/voters/{self._voter_id('g.teste@ints.org.br')}/delete")
        html = self.client.get(f"/admin/voters?evento={self.ev['slug']}").get_data(as_text=True)
        self.assertIn("Histórico de Votos Excluídos", html)
        self.assertIn("g.teste@ints.org.br", html)


if __name__ == "__main__":
    unittest.main()

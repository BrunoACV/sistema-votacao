"""
app/event_focus.py - Evento em foco do visitante.

Toda tela aberta com um evento explicito (/e/<slug>/..., ?evento=...) grava esse
evento na sessao. Telas abertas sem evento (/vote, /results, /admin, voltar do
catalogo /eventos) usam o evento em foco, e so caem no evento padrao quando a
pessoa nunca escolheu nenhum ou quando o evento em foco deixou de existir.
"""

from typing import Any, Dict, Optional

from flask import session

import app.db as db

SESSION_KEY = "evento_foco"


def remember(event: Optional[Dict[str, Any]]) -> None:
    if event and event.get("slug") and session.get(SESSION_KEY) != event["slug"]:
        session[SESSION_KEY] = event["slug"]


def focused_event() -> Optional[Dict[str, Any]]:
    slug = session.get(SESSION_KEY)
    if not slug:
        return None
    ev = db.get_event_by_slug(str(slug))
    if not ev:
        session.pop(SESSION_KEY, None)
    return ev


def focused_or_default() -> Dict[str, Any]:
    return focused_event() or db.get_default_event()

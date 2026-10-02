"""
app/routes/admin.py - Administrative Moderation and Auditing Blueprint.
INTS Institutional Voting System with Multi-Event Support.

Handles:
- Authentication decorator (@admin_required) supporting session cookie & API headers.
- Admin login, session issuance, and logout.
- Moderation dashboard with candidate metrics, photo display, and statistics scoped to events.
- Multi-event creation, status toggling, and event switching.
- Cascade deletion of candidates (SQLite foreign keys + filesystem unlinking).
- Institutional voter audit listing (@ints.org.br voters and their ratings).
- Scoped vote resetting per event.
"""

import functools
import hmac
import logging
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import db, storage
from app.config import Config

logger = logging.getLogger(__name__)

# Register blueprint with /admin prefix
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ==============================================================================
# Helper Functions & Authentication Decorator
# ==============================================================================

def is_safe_redirect_url(target: Optional[str]) -> bool:
    """
    Ensure the redirect URL is a safe local path to prevent open redirect vulnerabilities.
    Must start with '/' and must not contain schema or network location (e.g. '//evil.com').
    """
    if not target or not isinstance(target, str):
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/") and not target.startswith("//")


def get_safe_referrer(default_endpoint: str = "admin.dashboard") -> str:
    """
    Returns the referrer URL if it belongs to the same host and is a safe local path,
    otherwise returns the default endpoint URL.
    """
    ref = request.referrer
    if ref:
        parsed = urlparse(ref)
        if (not parsed.netloc or parsed.netloc == request.host) and parsed.path.startswith("/"):
            return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return url_for(default_endpoint)


def resolve_admin_event(target: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolves the targeted event for admin views by slug, id, or defaults to the primary event.
    """
    if target:
        target_str = str(target).strip()
        ev = db.get_event_by_slug(target_str)
        if not ev and target_str.isdigit():
            ev = db.get_event_by_id(int(target_str))
        if ev:
            return ev
    return db.get_default_event()


def is_admin_authenticated() -> bool:
    """
    Evaluates whether the incoming request contains valid administrative credentials.
    
    Checks:
    1. Active session cookie: session.get("is_admin") is True
    2. Header: X-Admin-Password
    3. Header: Authorization: Bearer <password>
    
    Uses hmac.compare_digest for constant-time comparison against current ADMIN_PASSWORD.
    """
    # 1. Session check
    if session.get("is_admin") is True:
        return True

    expected_password = current_app.config.get("ADMIN_PASSWORD") or Config.ADMIN_PASSWORD or "admin123"

    # 2. X-Admin-Password header check
    header_pass = request.headers.get("X-Admin-Password")
    if header_pass and hmac.compare_digest(str(header_pass).strip(), str(expected_password).strip()):
        session["is_admin"] = True
        return True

    # 3. Authorization: Bearer <password> header check
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if hmac.compare_digest(str(token), str(expected_password).strip()):
                session["is_admin"] = True
                return True

    return False


def admin_required(view_func: Callable) -> Callable:
    """
    Decorator protecting administrative routes.
    
    Enforces:
    - If authenticated: proceeds to view function.
    - If unauthenticated and request is API/JSON/Header/Mutation: returns HTTP 401 Unauthorized JSON.
    - If unauthenticated and request is browser navigation (GET with text/html): redirects to /admin/login.
    """
    @functools.wraps(view_func)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if is_admin_authenticated():
            return view_func(*args, **kwargs)

        # Detect if request is programmatic or expecting non-HTML response
        accept_header = request.headers.get("Accept", "")
        has_auth_headers = (
            request.headers.get("X-Admin-Password") is not None
            or request.headers.get("Authorization") is not None
        )
        is_json_request = (
            request.is_json
            or "application/json" in accept_header
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        is_browser_nav = (
            request.method == "GET"
            and "text/html" in accept_header
            and not has_auth_headers
            and not is_json_request
        )

        if is_browser_nav:
            flash("Por favor, faça login com a senha administrativa para acessar o painel.", "warning")
            return redirect(url_for("admin.login", next=request.path))

        # Programmatic / API / Mutation unauthorized access
        return jsonify({
            "status": "error",
            "error": "Unauthorized",
            "message": "Acesso administrativo não autorizado. Forneça credenciais válidas via sessão ou cabeçalho."
        }), 401

    return wrapped_view


# ==============================================================================
# Authentication Routes
# ==============================================================================

@admin_bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """
    Admin login route.
    GET: Displays login form (or redirects to dashboard if already authenticated).
    POST: Validates admin password. Sets session cookie or returns 401 Unauthorized.
    """
    if request.method == "GET":
        if is_admin_authenticated():
            next_url = request.args.get("next")
            if is_safe_redirect_url(next_url):
                return redirect(next_url)
            return redirect(url_for("admin.dashboard"))
        return render_template("admin_login.html", next_url=request.args.get("next", ""))

    expected_password = current_app.config.get("ADMIN_PASSWORD") or Config.ADMIN_PASSWORD or "admin123"

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        submitted_pass = payload.get("password", "")
        next_url = payload.get("next")
    else:
        submitted_pass = request.form.get("password", "")
        next_url = request.form.get("next")

    if not is_safe_redirect_url(next_url):
        next_url = url_for("admin.dashboard")

    if not submitted_pass or not hmac.compare_digest(str(submitted_pass).strip(), str(expected_password).strip()):
        logger.warning("Tentativa de login administrativo falhou. IP: %s", request.remote_addr)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({
                "status": "error",
                "error": "Unauthorized",
                "message": "Senha administrativa incorreta."
            }), 401
        
        flash("Senha administrativa incorreta. Tente novamente.", "error")
        return render_template("admin_login.html", next_url=next_url), 401

    session["is_admin"] = True
    session.permanent = True
    logger.info("Login administrativo bem-sucedido. IP: %s", request.remote_addr)

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": "Autenticado com sucesso.",
            "redirect": next_url
        }), 200

    flash("Autenticação realizada com sucesso. Bem-vindo ao painel administrativo!", "success")
    return redirect(next_url)


@admin_bp.route("/logout", methods=["GET", "POST"])
def logout() -> Any:
    """
    Clears administrative session and redirects to login screen.
    """
    session.pop("is_admin", None)
    logger.info("Sessão administrativa encerrada.")

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"status": "ok", "message": "Sessão encerrada com sucesso."}), 200

    flash("Sessão administrativa encerrada com sucesso.", "info")
    return redirect(url_for("admin.login"))


# ==============================================================================
# Dashboard & Moderation Routes
# ==============================================================================

@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@admin_required
def dashboard() -> Any:
    """
    Moderation dashboard listing all participants, statistics, and events.
    Supports switching between voting events via ?evento=<slug_or_id>.
    """
    events = db.list_events()
    current_event = resolve_admin_event(request.args.get("evento") or request.args.get("event"))

    summary = db.get_voting_summary(current_event["id"])
    participants = db.get_leaderboard(current_event["id"])
    voters = db.get_voters_audit(current_event["id"])

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "current_event": current_event,
            "events": events,
            "summary": summary,
            "participants": participants,
            "voters": voters
        }), 200

    return render_template(
        "admin.html",
        current_event=current_event,
        events=events,
        summary=summary,
        participants=participants,
        voters=voters
    )


# ==============================================================================
# Event Creation & Management Routes
# ==============================================================================

@admin_bp.route("/events/new", methods=["GET", "POST"])
@admin_required
def create_event() -> Any:
    """
    Endpoint for creating a new distinct voting event with selectable color theme.
    """
    if request.method == "GET":
        events = db.list_events()
        return render_template("admin_event_new.html", events=events, selected_theme="dracula")

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        nome = str(payload.get("nome") or "").strip()
        slug = str(payload.get("slug") or "").strip()
        descricao = str(payload.get("descricao") or "").strip()
        tema = str(payload.get("tema") or "dracula").strip().lower()
        ativo = 1 if payload.get("ativo", True) else 0
    else:
        nome = str(request.form.get("nome") or "").strip()
        slug = str(request.form.get("slug") or "").strip()
        descricao = str(request.form.get("descricao") or "").strip()
        tema = str(request.form.get("tema") or "dracula").strip().lower()
        ativo = 1 if request.form.get("ativo") in ("1", "on", "true", "True") else 0

    if not nome:
        msg = "O nome do evento é obrigatório."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_event_new.html", nome=nome, slug=slug, descricao=descricao, tema=tema, selected_theme=tema, ativo=ativo), 400

    try:
        new_event = db.create_event(nome=nome, slug=slug, descricao=descricao, tema=tema, ativo=ativo)
    except (db.DuplicateEventSlugError, ValueError) as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_event_new.html", nome=nome, slug=slug, descricao=descricao, tema=tema, selected_theme=tema, ativo=ativo), 400
    except Exception as exc:
        msg = f"Erro ao criar evento: {str(exc)}"
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
        flash(msg, "error")
        return render_template("admin_event_new.html", nome=nome, slug=slug, descricao=descricao, tema=tema, selected_theme=tema, ativo=ativo), 500

    success_msg = f"Evento '{new_event['nome']}' criado com sucesso com o tema {new_event.get('tema', 'dracula').capitalize()}! Ele opera em paralelo de forma totalmente independente."
    logger.info("Novo evento criado pelo moderador: ID %d, Slug %s, Tema %s", new_event["id"], new_event["slug"], new_event.get("tema"))

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "event": new_event,
            "redirect_url": url_for("admin.dashboard", evento=new_event["slug"])
        }), 201

    flash(success_msg, "success")
    return redirect(url_for("admin.dashboard", evento=new_event["slug"]))


@admin_bp.route("/events/<int:event_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_event_status(event_id: int) -> Any:
    """
    Toggles an event between active (voting open) and inactive (voting paused/closed).
    """
    event = db.get_event_by_id(event_id)
    if not event:
        msg = f"Evento #{event_id} não encontrado."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    new_status = 0 if event["ativo"] else 1
    updated = db.update_event(event_id=event_id, ativo=new_status)
    status_label = "aberto para votação" if new_status else "pausado / encerrado"
    success_msg = f"O evento '{event['nome']}' agora está {status_label}."
    logger.info("Status do evento #%d alterado para %d", event_id, new_status)

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "event_id": event_id,
            "ativo": new_status,
            "event": updated
        }), 200

    flash(success_msg, "success")
    return redirect(get_safe_referrer("admin.dashboard"))


@admin_bp.route("/events/<int:event_id>/delete", methods=["POST"])
@admin_required
def delete_event(event_id: int) -> Any:
    """
    Deletes an event and cascade-deletes all its participants, voters, and votes.
    Safely unlinks associated photo files.
    """
    all_events = db.list_events()
    if len(all_events) <= 1:
        msg = "Não é permitido excluir o único evento existente no sistema. Crie outro evento antes de remover este."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    deleted_data = db.delete_event_by_id(event_id)
    if not deleted_data:
        msg = f"Evento #{event_id} não encontrado ou já foi excluído."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    for photo in deleted_data.get("photos_to_unlink", []):
        storage.delete_photo(photo, upload_folder=upload_folder)

    success_msg = f"O evento '{deleted_data['nome']}' e todos os seus participantes e votos foram excluídos com sucesso."
    logger.info("Evento #%d excluído pelo moderador.", event_id)

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "deleted_id": event_id,
            "deleted_event": deleted_data
        }), 200

    flash(success_msg, "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/participants/<int:participant_id>/delete", methods=["POST"])
@admin_required
def delete_participant(participant_id: int) -> Any:
    """
    Cascade deletion endpoint for a candidate.
    """
    deleted_data = db.delete_participant_by_id(participant_id)
    if not deleted_data:
        msg = f"Candidato #{participant_id} não encontrado ou já foi excluído."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    foto_filename = deleted_data.get("foto_filename")
    photo_unlinked = False
    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    if foto_filename:
        photo_unlinked = storage.delete_photo(foto_filename, upload_folder=upload_folder)

    candidate_name = deleted_data.get("nome_completo", f"#{participant_id}")
    success_msg = f"Candidato '{candidate_name}' (ID: {participant_id}) e seus votos associados foram excluídos com sucesso."
    logger.info("Candidato excluído com sucesso: ID %d, Foto: %s (unlinked: %s)", participant_id, foto_filename, photo_unlinked)

    if (
        request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "deleted_id": participant_id,
            "deleted_candidate": deleted_data,
            "photo_unlinked": photo_unlinked
        }), 200

    flash(success_msg, "success")
    return redirect(get_safe_referrer("admin.dashboard"))


@admin_bp.route("/voters", methods=["GET"])
@admin_required
def voters_audit() -> Any:
    """
    Dedicated audit endpoint listing institutional voters (@ints.org.br) and their submitted ratings.
    Supports filtering by event.
    """
    events = db.list_events()
    event_arg = request.args.get("evento") or request.args.get("event")

    if event_arg == "todos":
        current_event = None
        voters = db.get_voters_audit(event_id=-1)
        summary = db.get_voting_summary(event_id=-1)
    else:
        current_event = resolve_admin_event(event_arg)
        voters = db.get_voters_audit(event_id=current_event["id"])
        summary = db.get_voting_summary(event_id=current_event["id"])

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "current_event": current_event,
            "events": events,
            "total_voters": len(voters),
            "voters": voters,
            "summary": summary
        }), 200

    return render_template(
        "admin_voters.html",
        current_event=current_event,
        events=events,
        voters=voters,
        summary=summary
    )


@admin_bp.route("/voters/<int:voter_id>/delete", methods=["POST"])
@admin_required
def delete_voter(voter_id: int) -> Any:
    """
    Cascade deletion endpoint for a voter and their cast vote.
    """
    deleted_data = db.delete_vote_by_voter_id(voter_id)
    if not deleted_data:
        msg = f"Eleitor #{voter_id} não encontrado ou já foi excluído."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(get_safe_referrer("admin.voters_audit"))

    voter_email = deleted_data.get("email", f"#{voter_id}")
    success_msg = f"O voto do colaborador '{voter_email}' foi removido com sucesso. O e-mail foi liberado para votar novamente caso necessário."
    logger.info("Voto removido pelo moderador: Voter ID %d (%s)", voter_id, voter_email)

    if (
        request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "deleted_voter_id": voter_id,
            "voter_email": voter_email
        }), 200

    flash(success_msg, "success")
    return redirect(get_safe_referrer("admin.voters_audit"))


@admin_bp.route("/votes/reset", methods=["POST"])
@admin_required
def reset_votes() -> Any:
    """
    Purges recorded votes and voters from the database for a specific event.
    """
    event_id = None
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        event_id = payload.get("event_id") or payload.get("evento")
    else:
        event_id = request.form.get("event_id") or request.form.get("evento") or request.args.get("evento")

    target_event_id = None
    event_name = "selecionado"
    if event_id and str(event_id).strip() != "-1" and str(event_id).strip() != "todos":
        ev = db.get_event_by_id(int(event_id)) if str(event_id).isdigit() else db.get_event_by_slug(str(event_id))
        if ev:
            target_event_id = ev["id"]
            event_name = ev["nome"]
    else:
        ev = db.get_default_event()
        if ev:
            target_event_id = ev["id"]
            event_name = ev["nome"]

    deleted_count = db.reset_all_votes(event_id=target_event_id)
    success_msg = f"Todos os votos ({deleted_count}) do evento '{event_name}' foram zerados com sucesso."
    logger.info("Todos os votos foram zerados pelo moderador para o evento #%s. Total de votos removidos: %d", str(target_event_id), deleted_count)

    if (
        request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "event_id": target_event_id,
            "deleted_count": deleted_count
        }), 200

    flash(success_msg, "success")
    return redirect(get_safe_referrer("admin.voters_audit"))

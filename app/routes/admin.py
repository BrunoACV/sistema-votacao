"""
app/routes/admin.py - Administrative Moderation and Auditing Blueprint.
INTS Institutional Voting System.

Handles:
- Authentication decorator (@admin_required) supporting session cookie & API headers.
- Admin login, session issuance, and logout.
- Moderation dashboard with candidate metrics, photo display, and statistics.
- Cascade deletion of candidates (SQLite foreign keys + filesystem unlinking).
- Institutional voter audit listing (@ints.org.br voters and their ratings).
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


def get_safe_referrer(default_endpoint: str = "admin.voters_audit") -> str:
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
        return True

    # 3. Authorization: Bearer <password> header check
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if hmac.compare_digest(str(token), str(expected_password).strip()):
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
        is_state_mutation = request.method in ("POST", "PUT", "DELETE", "PATCH")
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
    # If already logged in, redirect straight to dashboard or next url
    if request.method == "GET":
        if is_admin_authenticated():
            next_url = request.args.get("next")
            if is_safe_redirect_url(next_url):
                return redirect(next_url)
            return redirect(url_for("admin.dashboard"))
        return render_template("admin_login.html", next_url=request.args.get("next", ""))

    # POST: Authentication attempt
    expected_password = current_app.config.get("ADMIN_PASSWORD") or Config.ADMIN_PASSWORD or "admin123"

    # Extract password from JSON body or Form payload
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        submitted_pass = payload.get("password", "")
        next_url = payload.get("next")
    else:
        submitted_pass = request.form.get("password", "")
        next_url = request.form.get("next")

    if not is_safe_redirect_url(next_url):
        next_url = url_for("admin.dashboard")

    # Validate submitted password using constant-time comparison
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

    # Authentication succeeded: set session
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
    Moderation dashboard listing all participants, statistics, and voter audit log.
    Accessible via /admin or /admin/.
    """
    summary = db.get_voting_summary()
    participants = db.get_leaderboard()
    voters = db.get_voters_audit()

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "summary": summary,
            "participants": participants,
            "voters": voters
        }), 200

    return render_template(
        "admin.html",
        summary=summary,
        participants=participants,
        voters=voters
    )


@admin_bp.route("/participants/<int:participant_id>/delete", methods=["POST"])
@admin_required
def delete_participant(participant_id: int) -> Any:
    """
    Cascade deletion endpoint for a candidate.
    
    Guarantees:
    1. Enforces administrative authentication (@admin_required).
    2. Deletes candidate record from SQLite database.
    3. Triggers SQLite PRAGMA foreign_keys ON DELETE CASCADE, purging all associated votes.
    4. Safely unlinks the photo file from static/uploads/ via storage.delete_photo.
    5. Returns 404 if participant is not found.
    6. Redirects back to dashboard with flash message or returns JSON for API calls.
    """
    # 1. Database cascade deletion
    deleted_data = db.delete_participant_by_id(participant_id)
    if not deleted_data:
        msg = f"Candidato #{participant_id} não encontrado ou já foi excluído."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    # 2. Filesystem photo unlinking
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
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/voters", methods=["GET"])
@admin_required
def voters_audit() -> Any:
    """
    Dedicated audit endpoint listing institutional voters (@ints.org.br) and their submitted ratings.
    """
    voters = db.get_voters_audit()
    summary = db.get_voting_summary()

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "total_voters": len(voters),
            "voters": voters,
            "summary": summary
        }), 200

    return render_template(
        "admin_voters.html",
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
    Purges all recorded votes and voters from the database.
    """
    deleted_count = db.reset_all_votes()
    success_msg = f"Todos os votos ({deleted_count}) foram zerados com sucesso. O concurso está pronto para nova votação."
    logger.info("Todos os votos foram zerados pelo moderador. Total de votos removidos: %d", deleted_count)

    if (
        request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "deleted_count": deleted_count
        }), 200

    flash(success_msg, "success")
    return redirect(get_safe_referrer("admin.voters_audit"))

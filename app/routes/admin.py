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

from app import db, event_focus, storage
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
            event_focus.remember(ev)
            return ev
    return event_focus.focused_or_default()


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
    - If authenticated:
      - If user must change password and not on change-password/logout route, forces redirect.
      - Otherwise proceeds to view function.
    - If unauthenticated and request is API/JSON/Header/Mutation: returns HTTP 401 Unauthorized JSON.
    - If unauthenticated and request is browser navigation (GET with text/html): redirects to /admin/login.
    """
    @functools.wraps(view_func)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if is_admin_authenticated():
            if session.get("must_change_password"):
                if request.endpoint not in ("admin.change_password", "admin.logout"):
                    if request.is_json or "application/json" in request.headers.get("Accept", ""):
                        return jsonify({
                            "status": "error",
                            "error": "PasswordChangeRequired",
                            "message": "Você deve alterar sua senha no primeiro acesso antes de continuar.",
                            "redirect": url_for("admin.change_password")
                        }), 403
                    flash("Por motivos de segurança, você deve redefinir sua senha no primeiro acesso.", "warning")
                    return redirect(url_for("admin.change_password"))
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
            flash("Por favor, faça login com suas credenciais de administrador para acessar o painel.", "warning")
            return redirect(url_for("admin.login", next=request.path))

        # Programmatic / API / Mutation unauthorized access
        return jsonify({
            "status": "error",
            "error": "Unauthorized",
            "message": "Acesso administrativo não autorizado. Forneça credenciais válidas via sessão ou cabeçalho."
        }), 401

    return wrapped_view


# ==============================================================================
# Authentication & Password Routes
# ==============================================================================

@admin_bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """
    Admin login route.
    GET: Displays login form (or redirects to dashboard if already authenticated).
    POST: Validates admin credentials (username + password, or fallback master password).
    Sets session cookie or returns 401 Unauthorized.
    """
    if request.method == "GET":
        if is_admin_authenticated():
            if session.get("must_change_password"):
                return redirect(url_for("admin.change_password"))
            next_url = request.args.get("next")
            if is_safe_redirect_url(next_url):
                return redirect(next_url)
            return redirect(url_for("admin.dashboard"))
        return render_template("admin_login.html", next_url=request.args.get("next", ""))

    expected_password = current_app.config.get("ADMIN_PASSWORD") or Config.ADMIN_PASSWORD or "admin123"

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        submitted_user = str(payload.get("username") or "").strip()
        submitted_pass = str(payload.get("password") or "").strip()
        next_url = payload.get("next")
    else:
        submitted_user = str(request.form.get("username") or "").strip()
        submitted_pass = str(request.form.get("password") or "").strip()
        next_url = request.form.get("next")

    if not is_safe_redirect_url(next_url):
        next_url = url_for("admin.dashboard")

    # Autenticação
    if submitted_user:
        auth_user = db.authenticate_user(submitted_user, submitted_pass)
        if not auth_user:
            logger.warning("Falha de autenticação para usuário '%s' do IP: %s", submitted_user, request.remote_addr)
            if request.is_json or "application/json" in request.headers.get("Accept", ""):
                return jsonify({
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Usuário ou senha incorretos."
                }), 401
            flash("Usuário ou senha incorretos. Verifique suas credenciais.", "error")
            return render_template("admin_login.html", next_url=next_url, username=submitted_user), 401

        session["is_admin"] = bool(auth_user.get("is_admin", 1))
        session["user_id"] = auth_user["id"]
        session["username"] = auth_user["username"]
        session["user_name"] = auth_user.get("nome") or auth_user["username"].capitalize()
        session["must_change_password"] = bool(auth_user.get("must_change_password", 0))
        session.permanent = True
        logger.info("Login realizado com sucesso pelo usuário '%s' (ID %d). IP: %s", auth_user["username"], auth_user["id"], request.remote_addr)

        target_redirect = url_for("admin.change_password") if session["must_change_password"] else next_url
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({
                "status": "ok",
                "message": "Autenticado com sucesso.",
                "must_change_password": session["must_change_password"],
                "redirect": target_redirect
            }), 200

        if session["must_change_password"]:
            flash("Bem-vindo! Como este é seu primeiro acesso, defina uma nova senha para sua conta.", "info")
            return redirect(url_for("admin.change_password"))

        flash(f"Bem-vindo, {session['user_name']}!", "success")
        return redirect(next_url)

    else:
        # Fallback para master password (compatibilidade com suíte de testes legada)
        if not submitted_pass or not hmac.compare_digest(str(submitted_pass).strip(), str(expected_password).strip()):
            logger.warning("Tentativa de login com senha mestra falhou. IP: %s", request.remote_addr)
            if request.is_json or "application/json" in request.headers.get("Accept", ""):
                return jsonify({
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Senha administrativa incorreta."
                }), 401
            flash("Senha administrativa incorreta. Tente novamente.", "error")
            return render_template("admin_login.html", next_url=next_url), 401

        session["is_admin"] = True
        session["user_id"] = None
        session["username"] = "admin"
        session["user_name"] = "Administrador Geral"
        session["must_change_password"] = False
        session.permanent = True
        logger.info("Login administrativo mestre bem-sucedido. IP: %s", request.remote_addr)

        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({
                "status": "ok",
                "message": "Autenticado com sucesso.",
                "redirect": next_url
            }), 200

        flash("Autenticação realizada com sucesso. Bem-vindo ao painel administrativo!", "success")
        return redirect(next_url)


@admin_bp.route("/change-password", methods=["GET", "POST"])
@admin_required
def change_password() -> Any:
    """
    Allows the logged-in administrator to change their password.
    Mandatory on first access if must_change_password is true.
    """
    is_first = session.get("must_change_password", False)

    if request.method == "GET":
        return render_template("admin_change_password.html", is_first_access=is_first)

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        new_password = str(payload.get("new_password") or "").strip()
        confirm_password = str(payload.get("confirm_password") or "").strip()
    else:
        new_password = str(request.form.get("new_password") or "").strip()
        confirm_password = str(request.form.get("confirm_password") or "").strip()

    if not new_password:
        msg = "A nova senha não pode estar em branco."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_change_password.html", is_first_access=is_first), 400

    if len(new_password) < 6:
        msg = "A nova senha deve ter no mínimo 6 caracteres."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_change_password.html", is_first_access=is_first), 400

    if new_password.lower() == "trocar":
        msg = "A nova senha não pode ser a senha padrão ('trocar'). Por favor, escolha uma senha segura."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_change_password.html", is_first_access=is_first), 400

    if new_password != confirm_password:
        msg = "A confirmação de senha não confere com a nova senha digitada."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template("admin_change_password.html", is_first_access=is_first), 400

    user_id = session.get("user_id")
    if user_id:
        try:
            db.update_user_password(user_id, new_password, must_change_password=0)
        except Exception as exc:
            msg = f"Erro ao atualizar senha: {str(exc)}"
            if request.is_json or "application/json" in request.headers.get("Accept", ""):
                return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
            flash(msg, "error")
            return render_template("admin_change_password.html", is_first_access=is_first), 500

    session["must_change_password"] = False
    logger.info("Senha alterada com sucesso para o usuário ID %s (%s)", str(user_id), session.get("username"))

    success_msg = "Sua nova senha foi salva com sucesso! Você agora possui acesso irrestrito ao painel."
    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "redirect": url_for("admin.dashboard")
        }), 200

    flash(success_msg, "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/logout", methods=["GET", "POST"])
def logout() -> Any:
    """
    Clears administrative session and redirects to login screen.
    """
    session.pop("is_admin", None)
    session.pop("user_id", None)
    session.pop("username", None)
    session.pop("user_name", None)
    session.pop("must_change_password", None)
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
        campos_personalizados = payload.get("campos_personalizados")
    else:
        nome = str(request.form.get("nome") or "").strip()
        slug = str(request.form.get("slug") or "").strip()
        descricao = str(request.form.get("descricao") or "").strip()
        tema = str(request.form.get("tema") or "dracula").strip().lower()
        ativo = 1 if request.form.get("ativo") in ("1", "on", "true", "True") else 0
        campos_personalizados = request.form.get("campos_personalizados")

    if not nome:
        msg = "O nome do evento é obrigatório."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template(
            "admin_event_new.html",
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 400

    try:
        new_event = db.create_event(
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        )
    except (db.DuplicateEventSlugError, ValueError) as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template(
            "admin_event_new.html",
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 400
    except Exception as exc:
        msg = f"Erro ao criar evento: {str(exc)}"
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
        flash(msg, "error")
        return render_template(
            "admin_event_new.html",
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 500

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


@admin_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_event(event_id: int) -> Any:
    """
    Endpoint for editing an existing voting event and configuring/modifying its custom registration fields.
    """
    event = db.get_event_by_id(event_id)
    if not event:
        msg = f"Evento #{event_id} não encontrado."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.dashboard"))

    events = db.list_events()

    if request.method == "GET":
        campos_personalizados = event.get("campos_personalizados_parsed")
        if campos_personalizados is None:
            campos_personalizados = db.parse_custom_fields(event.get("campos_personalizados"))

        return render_template(
            "admin_event_edit.html",
            event=event,
            events=events,
            nome=event["nome"],
            slug=event["slug"],
            descricao=event.get("descricao", ""),
            tema=event.get("tema", "dracula"),
            selected_theme=event.get("tema", "dracula"),
            ativo=event["ativo"],
            campos_personalizados=campos_personalizados
        )

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        nome = str(payload.get("nome") or "").strip()
        slug = str(payload.get("slug") or "").strip()
        descricao = str(payload.get("descricao") or "").strip()
        tema = str(payload.get("tema") or event.get("tema", "dracula")).strip().lower()
        ativo = 1 if payload.get("ativo", True) else 0
        campos_personalizados = payload.get("campos_personalizados")
    else:
        nome = str(request.form.get("nome") or "").strip()
        slug = str(request.form.get("slug") or "").strip()
        descricao = str(request.form.get("descricao") or "").strip()
        tema = str(request.form.get("tema") or event.get("tema", "dracula")).strip().lower()
        ativo = 1 if request.form.get("ativo") in ("1", "on", "true", "True") else 0
        campos_personalizados = request.form.get("campos_personalizados")

    if not nome:
        msg = "O nome do evento é obrigatório."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template(
            "admin_event_edit.html",
            event=event,
            events=events,
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 400

    try:
        updated = db.update_event(
            event_id=event_id,
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        )
    except (db.DuplicateEventSlugError, ValueError) as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return render_template(
            "admin_event_edit.html",
            event=event,
            events=events,
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 400
    except Exception as exc:
        msg = f"Erro ao atualizar evento: {str(exc)}"
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
        flash(msg, "error")
        return render_template(
            "admin_event_edit.html",
            event=event,
            events=events,
            nome=nome,
            slug=slug,
            descricao=descricao,
            tema=tema,
            selected_theme=tema,
            ativo=ativo,
            campos_personalizados=campos_personalizados
        ), 500

    success_msg = f"Evento '{updated['nome']}' e seus campos de inscrição foram atualizados com sucesso!"
    logger.info("Evento #%d atualizado pelo moderador: Slug %s, Tema %s", updated["id"], updated["slug"], updated.get("tema"))

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "event": updated,
            "redirect_url": url_for("admin.dashboard", evento=updated["slug"])
        }), 200

    flash(success_msg, "success")
    return redirect(url_for("admin.dashboard", evento=updated["slug"]))


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
        ev = event_focus.focused_or_default()
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


# ==============================================================================
# User Management Routes (Administrators)
# ==============================================================================

@admin_bp.route("/users", methods=["GET"])
@admin_required
def list_users() -> Any:
    """
    Displays the user administration page with all registered admin users
    and a creation form.
    """
    users = db.list_users()
    events = db.list_events()
    current_event = resolve_admin_event(request.args.get("evento") or request.args.get("event"))

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "users": users
        }), 200

    return render_template(
        "admin_users.html",
        users=users,
        events=events,
        current_event=current_event
    )


@admin_bp.route("/users/new", methods=["POST"])
@admin_required
def create_user() -> Any:
    """
    Creates a new administrative user.
    All created users are admins and required to change password on first access.
    """
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username") or "").strip().lower()
        nome = str(payload.get("nome") or "").strip()
        password = str(payload.get("password") or "trocar").strip()
    else:
        username = str(request.form.get("username") or "").strip().lower()
        nome = str(request.form.get("nome") or "").strip()
        password = str(request.form.get("password") or "trocar").strip()

    if not username:
        msg = "O nome de usuário é obrigatório."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))

    if not nome:
        nome = username.capitalize()

    if not password:
        password = "trocar"

    try:
        new_user = db.create_user(
            username=username,
            password=password,
            nome=nome,
            is_admin=1,
            must_change_password=1
        )
    except db.DuplicateUsernameError as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Bad Request", "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))
    except Exception as exc:
        msg = f"Erro ao criar usuário: {str(exc)}"
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))

    success_msg = f"Usuário administrador @{new_user['username']} ({new_user['nome']}) cadastrado com sucesso! A senha inicial é '{password}' e deverá ser alterada no primeiro acesso."
    logger.info("Novo usuário admin criado: ID %d (@%s)", new_user["id"], new_user["username"])

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "user": new_user
        }), 201

    flash(success_msg, "success")
    return redirect(url_for("admin.list_users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int) -> Any:
    """
    Deletes an administrator account. Prevents deleting self or the last administrator.
    """
    current_uid = session.get("user_id")
    if current_uid and current_uid == user_id:
        msg = "Você não pode excluir sua própria conta enquanto estiver conectado."
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Forbidden", "message": msg}), 403
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))

    try:
        db.delete_user(user_id)
    except ValueError as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Forbidden", "message": msg}), 403
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))
    except db.UserNotFoundError as exc:
        msg = str(exc)
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Not Found", "message": msg}), 404
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))
    except Exception as exc:
        msg = f"Erro ao excluir usuário: {str(exc)}"
        if request.is_json or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "error", "error": "Server Error", "message": msg}), 500
        flash(msg, "error")
        return redirect(url_for("admin.list_users"))

    success_msg = "Usuário administrador excluído com sucesso."
    logger.info("Usuário admin ID %d excluído com sucesso.", user_id)

    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "status": "ok",
            "message": success_msg,
            "deleted_user_id": user_id
        }), 200

    flash(success_msg, "success")
    return redirect(url_for("admin.list_users"))


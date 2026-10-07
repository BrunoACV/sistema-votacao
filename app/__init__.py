"""
app/__init__.py - Application factory for INTS Institutional Voting System.
Configures Flask, database lifecycle, blueprints, static routing, security headers, and error handlers.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional, Type, Union

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    Response,
)

from app.config import Config, DevelopmentConfig, ProductionConfig, TestingConfig, get_config
from app.db import (
    init_app as init_db_app,
    close_db,
    DuplicateParticipantEmailError,
    VoterAlreadyVotedError,
    InvalidVoterEmailError,
    InvalidScoreError,
    MissingParticipantRatingError,
    ParticipantNotFoundError,
    DatabaseError,
)
from app.storage import (
    StorageValidationError,
    StorageFileTooLargeError,
    StorageInvalidFormatError,
)

__version__ = "1.0.0"


class ReverseProxied:
    """WSGI middleware that reads X-Script-Name header from the reverse proxy
    and sets SCRIPT_NAME so that Flask's url_for() generates correct prefixed URLs."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        script_name = environ.get("HTTP_X_SCRIPT_NAME", "")
        if script_name:
            environ["SCRIPT_NAME"] = script_name
            path_info = environ.get("PATH_INFO", "")
            if path_info.startswith(script_name):
                environ["PATH_INFO"] = path_info[len(script_name):]
        return self.app(environ, start_response)


def _wants_json_response() -> bool:
    """Determine whether client expects JSON or HTML response."""
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    if accept.accept_json and not accept.accept_html:
        return True
    if accept.accept_json and accept.accept_html:
        return accept["application/json"] > accept["text/html"]
    return False


def _create_error_response(status_code: int, error_title: str, message: str) -> Any:
    """Produce content-negotiated response for errors."""
    if _wants_json_response():
        return jsonify({
            "status": "error",
            "code": status_code,
            "error": error_title,
            "message": message,
        }), status_code

    try:
        return render_template(
            "error.html",
            status_code=status_code,
            title=error_title,
            message=message,
        ), status_code
    except Exception:
        # Resilient fallback if error.html is not yet created or fails rendering
        html = (
            f"<!DOCTYPE html><html lang='pt-BR'><head><title>{status_code} - {error_title}</title>"
            f"<meta charset='utf-8'><style>body{{font-family:sans-serif;margin:40px;color:#333;background:#F4F6F9;}}"
            f".card{{background:#fff;padding:24px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);max-width:600px;margin:auto;}}"
            f"h1{{color:#003366;margin-top:0;}}a{{color:#00A86B;text-decoration:none;font-weight:bold;}}</style></head>"
            f"<body><div class='card'><h1>{status_code} - {error_title}</h1><p>{message}</p>"
            f"<p><a href='/'>&larr; Voltar ao início</a></p></div></body></html>"
        )
        return Response(html, status=status_code, mimetype="text/html")


def create_app(config_input: Optional[Union[str, Type[Config], Config]] = None) -> Flask:
    """
    Flask Application Factory.
    
    Args:
        config_input: Configuration environment name ('development', 'testing', 'production'),
                      or a Config class, or None (defaults to APP_ENV or development).
                      
    Returns:
        Configured Flask instance.
    """
    # 1. Resolve configuration
    if isinstance(config_input, type) and issubclass(config_input, Config):
        config_cls = config_input
        config_cls.validate()
        config_cls.init_directories()
    elif isinstance(config_input, Config):
        config_cls = config_input.__class__
        config_cls.validate()
        config_cls.init_directories()
    elif isinstance(config_input, str) or config_input is None:
        config_cls = get_config(config_input)
    else:
        config_cls = DevelopmentConfig
        config_cls.validate()
        config_cls.init_directories()

    # 2. Set explicit root-level template and static paths
    template_dir = str(config_cls.BASE_DIR / "templates")
    static_dir = str(config_cls.BASE_DIR / "static")

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
        static_url_path="/static",
    )

    # 3. Load configuration into Flask
    app.config.from_object(config_cls)
    app.secret_key = config_cls.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config_cls.MAX_CONTENT_LENGTH
    app.config["DATABASE_PATH"] = str(config_cls.DATABASE_PATH)
    app.config["UPLOAD_FOLDER"] = str(config_cls.UPLOAD_FOLDER)
    app.config["ADMIN_PASSWORD"] = config_cls.ADMIN_PASSWORD

    # 4. Initialize Database & Context Teardown
    init_db_app(app)

    # 5. Diagnostic Health Endpoint
    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({
            "status": "ok",
            "app": "sistema_votacao_ints",
            "env": app.config.get("APP_ENV", "unknown"),
        }), 200

    # 6. Convenience upload route alias (/uploads/<filename>)
    @app.route("/uploads/<path:filename>", methods=["GET"])
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    # 7. Register Blueprints
    from app.routes.public import public_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(public_bp, url_prefix="")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # 8. Institutional Context Processors
    from app.db import AVAILABLE_THEMES, DEFAULT_THEME

    @app.context_processor
    def inject_institutional_metadata() -> Dict[str, Any]:
        return {
            "app_name": "Sistema de Votação Institucional INTS",
            "org_name": "Instituto Nacional de Tecnologia e Saúde",
            "org_domain": "ints.org.br",
            "available_themes": AVAILABLE_THEMES,
            "default_theme": DEFAULT_THEME,
        }

    # 8b. Evento em foco disponivel em toda tela (caixa "Evento Ativo" do menu do moderador).
    # A view que passa current_event no render_template prevalece sobre este valor.
    @app.context_processor
    def inject_focused_event() -> Dict[str, Any]:
        from flask import session as _session
        from app import event_focus
        if not _session.get("is_admin"):
            return {}
        try:
            return {"current_event": event_focus.focused_or_default()}
        except Exception:
            return {}

    # 9. Security Response Headers
    @app.after_request
    def set_security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # 10. HTTP Error Handlers
    @app.errorhandler(400)
    def handle_bad_request(e):
        msg = getattr(e, "description", "Requisição inválida ou dados incorretos.")
        return _create_error_response(400, "Requisição Inválida", msg)

    @app.errorhandler(401)
    def handle_unauthorized(e):
        msg = getattr(e, "description", "Acesso não autorizado. Autenticação necessária.")
        return _create_error_response(401, "Não Autorizado", msg)

    @app.errorhandler(403)
    def handle_forbidden(e):
        msg = getattr(e, "description", "Acesso proibido a este recurso.")
        return _create_error_response(403, "Acesso Proibido", msg)

    @app.errorhandler(404)
    def handle_not_found(e):
        msg = getattr(e, "description", "O recurso ou página solicitada não foi encontrado.")
        return _create_error_response(404, "Página Não Encontrada", msg)

    @app.errorhandler(413)
    def handle_file_too_large(e):
        max_mb = app.config.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024) / (1024 * 1024)
        msg = f"O arquivo enviado excede o limite máximo permitido de {max_mb:.0f}MB."
        return _create_error_response(413, "Arquivo Muito Grande", msg)

    @app.errorhandler(422)
    def handle_unprocessable_entity(e):
        msg = getattr(e, "description", "Não foi possível processar as instruções enviadas.")
        return _create_error_response(422, "Entidade Improcessável", msg)

    @app.errorhandler(500)
    def handle_internal_server_error(e):
        msg = "Ocorreu um erro interno inesperado no servidor. Nossa equipe foi notificada."
        return _create_error_response(500, "Erro Interno do Servidor", msg)

    # 11. Domain Exception Handlers (Mapping business exceptions directly)
    @app.errorhandler(DuplicateParticipantEmailError)
    def handle_duplicate_participant(e):
        return _create_error_response(400, "E-mail Já Cadastrado", str(e))

    @app.errorhandler(VoterAlreadyVotedError)
    def handle_voter_already_voted(e):
        return _create_error_response(422, "Voto Já Registrado", str(e))

    @app.errorhandler(InvalidVoterEmailError)
    def handle_invalid_voter_email(e):
        return _create_error_response(422, "E-mail Institucional Obrigatório", str(e))

    @app.errorhandler(InvalidScoreError)
    def handle_invalid_score(e):
        return _create_error_response(400, "Nota Inválida", str(e))

    @app.errorhandler(MissingParticipantRatingError)
    def handle_missing_rating(e):
        return _create_error_response(400, "Avaliação Incompleta", str(e))

    @app.errorhandler(ParticipantNotFoundError)
    def handle_participant_not_found(e):
        return _create_error_response(404, "Candidato Não Encontrado", str(e))

    @app.errorhandler(StorageFileTooLargeError)
    def handle_storage_file_too_large(e):
        return _create_error_response(413, "Foto Muito Grande", str(e))

    @app.errorhandler(StorageInvalidFormatError)
    def handle_storage_invalid_format(e):
        return _create_error_response(400, "Formato de Foto Inválido", str(e))

    @app.errorhandler(StorageValidationError)
    def handle_storage_validation(e):
        return _create_error_response(400, "Erro de Validação da Foto", str(e))

    # Apply reverse proxy middleware for prefix-aware URL generation
    app.wsgi_app = ReverseProxied(app.wsgi_app)

    return app

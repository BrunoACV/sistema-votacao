"""
app/routes/public.py - Public routes for INTS Institutional Voting System.
Handles candidate registration, institutional voting, events catalog, and leaderboard results.
Supports multiple distinct voting events operating concurrently.
"""

import re
from typing import Any, Dict, List, Optional
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    current_app,
    abort,
)

from app.db import (
    add_participant,
    list_participants,
    get_participant_by_email,
    get_participant_by_id,
    has_voter_voted,
    record_votes,
    record_single_vote,
    get_leaderboard,
    get_voting_summary,
    list_events,
    get_event_by_id,
    get_event_by_slug,
    get_default_event,
    DuplicateParticipantEmailError,
    VoterAlreadyVotedError,
    InvalidVoterEmailError,
    InvalidScoreError,
    MissingParticipantRatingError,
    ParticipantNotFoundError,
    EventNotFoundError,
    EventInactiveError,
    DatabaseError,
)
from app.storage import (
    save_photo,
    delete_photo,
    StorageValidationError,
    StorageFileTooLargeError,
    StorageInvalidFormatError,
)

public_bp = Blueprint("public", __name__)

EMAIL_DOMAIN_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@ints\.org\.br$", re.IGNORECASE)


def resolve_event(slug: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolves the targeted voting event from route slug, query param, or system default.
    Raises 404 if a specific event is requested but does not exist.
    """
    target = slug or request.args.get("evento") or request.args.get("event")
    if target:
        target_str = str(target).strip()
        ev = get_event_by_slug(target_str)
        if not ev and target_str.isdigit():
            ev = get_event_by_id(int(target_str))
        if not ev:
            abort(404, description=f"Evento de votação '{target}' não foi encontrado.")
        return ev
    return get_default_event()


@public_bp.route("/", methods=["GET"])
def index():
    """
    Entrypoint route. Redirects to /results of the active event.
    """
    return redirect(url_for("public.results"))


@public_bp.route("/eventos", methods=["GET"])
def events_catalog():
    """
    Public catalog listing all active and past voting events.
    Allows institutional users to select and participate in different events.
    """
    all_events = list_events()

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"events": all_events}), 200

    return render_template("events.html", events=all_events)


@public_bp.route("/register", methods=["GET", "POST"])
@public_bp.route("/e/<slug>/register", methods=["GET", "POST"])
def register(slug: Optional[str] = None):
    """
    Participant registration endpoint for a specific event. Handles multipart form upload and validation.
    """
    event = resolve_event(slug)

    if request.method in ["GET", "HEAD"]:
        return render_template("register.html", event=event, current_event=event)

    # Check if event is active
    if not event.get("ativo", 1):
        msg = f"As inscrições para o evento '{event['nome']}' estão encerradas no momento."
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"status": "error", "error": "Evento Encerrado", "message": msg}), 400
        flash(msg, "warning")
        return render_template("register.html", event=event, current_event=event), 400

    # Extract input fields (handling both multipart form and JSON payloads)
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        nome_completo = str(payload.get("nome_completo") or "").strip()
        email = str(payload.get("email") or "").strip().lower()
        funcao = str(payload.get("funcao") or "").strip()
        setor = str(payload.get("setor") or "").strip()
        descricao = str(payload.get("descricao") or "").strip()
        foto_file = None
    else:
        nome_completo = str(request.form.get("nome_completo") or "").strip()
        email = str(request.form.get("email") or "").strip().lower()
        funcao = str(request.form.get("funcao") or "").strip()
        setor = str(request.form.get("setor") or "").strip()
        descricao = str(request.form.get("descricao") or "").strip()
        foto_file = request.files.get("foto")

    dados_personalizados: Dict[str, Any] = {}

    def bad_request(msg: str):
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"status": "error", "error": "Requisição Inválida", "message": msg}), 400
        flash(msg, "error")
        return (
            render_template(
                "register.html",
                event=event,
                current_event=event,
                nome_completo=nome_completo,
                email=email,
                funcao=funcao,
                setor=setor,
                descricao=descricao,
                dados_personalizados=dados_personalizados,
            ),
            400,
        )

    # 1. Text Field Validations
    if not nome_completo or len(nome_completo) > 150:
        return bad_request("O nome completo do participante é obrigatório (máximo 150 caracteres).")

    if not email or "@" not in email or "." not in email:
        return bad_request("Informe um endereço de e-mail válido para o participante.")

    if funcao and len(funcao) > 100:
        return bad_request("A função / cargo do participante deve ter no máximo 100 caracteres.")

    if setor and len(setor) > 100:
        return bad_request("O setor / departamento do participante deve ter no máximo 100 caracteres.")

    if not descricao or len(descricao) > 500:
        return bad_request("A descrição da inscrição é obrigatória (máximo 500 caracteres).")

    # 2. Extract and Validate Custom Fields for this Event
    custom_fields = event.get("campos_personalizados_parsed")
    if custom_fields is None:
        custom_fields = db.parse_custom_fields(event.get("campos_personalizados"))

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        custom_payload = payload.get("dados_personalizados") or {}
        for field in custom_fields:
            fid = field["id"]
            val = custom_payload.get(fid)
            if val is None:
                val = payload.get(f"custom_{fid}")
            if val is None:
                val = payload.get(fid)

            val_str = str(val).strip() if val is not None else ""
            if field.get("obrigatorio") and not val_str:
                return bad_request(f"O campo '{field.get('label') or fid}' é obrigatório.")
            if val_str:
                dados_personalizados[fid] = val_str
    else:
        for field in custom_fields:
            fid = field["id"]
            val = request.form.get(f"custom_{fid}")
            if val is None:
                val = request.form.get(fid)

            val_str = str(val).strip() if val is not None else ""
            if field.get("obrigatorio") and not val_str:
                return bad_request(f"O campo '{field.get('label') or fid}' é obrigatório.")
            if val_str:
                dados_personalizados[fid] = val_str

    # 3. Check for Duplicate Participant Email in this specific event
    existing = get_participant_by_email(email, event_id=event["id"])
    if existing:
        return bad_request(f"O e-mail '{email}' já está cadastrado como participante neste evento.")

    # 4. Validate Photo Presence
    if not foto_file or not getattr(foto_file, "filename", ""):
        return bad_request("A foto do participante é obrigatória. Selecione um arquivo JPG, PNG ou WEBP.")

    # 5. Save Photo with UUID & Pillow Deep Validation
    foto_filename: Optional[str] = None
    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    try:
        foto_filename = save_photo(foto_file, upload_folder=upload_folder)
    except (StorageValidationError, StorageFileTooLargeError, StorageInvalidFormatError) as exc:
        return bad_request(f"Arquivo de foto inválido: {str(exc)}")
    except Exception as exc:
        return bad_request(f"Erro ao processar imagem da foto: {str(exc)}")

    # 6. Insert Participant Record (with Orphan Photo Rollback)
    try:
        participant_id = add_participant(
            nome_completo=nome_completo,
            email=email,
            descricao=descricao,
            foto_filename=foto_filename,
            funcao=funcao,
            setor=setor,
            dados_personalizados=dados_personalizados,
            event_id=event["id"],
        )
    except DuplicateParticipantEmailError as exc:
        if foto_filename:
            delete_photo(foto_filename, upload_folder=upload_folder)
        return bad_request(str(exc))
    except (EventNotFoundError, EventInactiveError) as exc:
        if foto_filename:
            delete_photo(foto_filename, upload_folder=upload_folder)
        return bad_request(str(exc))
    except Exception as exc:
        if foto_filename:
            delete_photo(foto_filename, upload_folder=upload_folder)
        if request.is_json:
            return jsonify({"status": "error", "error": "Erro no Banco de Dados", "message": str(exc)}), 500
        flash(f"Erro ao cadastrar participante: {str(exc)}", "error")
        return (
            render_template(
                "register.html",
                event=event,
                current_event=event,
                nome_completo=nome_completo,
                email=email,
                funcao=funcao,
                setor=setor,
                descricao=descricao,
                dados_personalizados=dados_personalizados,
            ),
            500,
        )

    # 6. Response Handling
    if request.is_json:
        return (
            jsonify({
                "status": "success",
                "message": "Participante cadastrado com sucesso!",
                "id": participant_id,
                "event_id": event["id"],
                "event_slug": event["slug"],
                "foto_filename": foto_filename,
            }),
            201,
        )

    if slug:
        return redirect(url_for("public.register_success", slug=event["slug"], id=participant_id))
    return redirect(url_for("public.register_success", id=participant_id, evento=event["slug"]))


@public_bp.route("/register/success", methods=["GET"])
@public_bp.route("/e/<slug>/register/success", methods=["GET"])
def register_success(slug: Optional[str] = None):
    """
    Registration completion confirmation screen.
    """
    event = resolve_event(slug)
    participant_id = request.args.get("id")
    participant = None
    if participant_id:
        try:
            participant = get_participant_by_id(int(participant_id))
        except (ValueError, TypeError):
            pass
    return render_template("register_success.html", participant=participant, event=event, current_event=event)


@public_bp.route("/vote", methods=["GET", "POST"])
@public_bp.route("/e/<slug>/vote", methods=["GET", "POST"])
def vote(slug: Optional[str] = None):
    """
    Public voting endpoint scoped to an event.
    Displays candidates and processes consolidated institutional evaluations.
    """
    event = resolve_event(slug)
    candidates = list_participants(event_id=event["id"])

    if request.method in ["GET", "HEAD"]:
        return render_template("vote.html", candidates=candidates, event=event, current_event=event)

    # Check if event is active
    if not event.get("ativo", 1):
        msg = f"A votação para o evento '{event['nome']}' está encerrada no momento."
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"status": "error", "error": "Votação Encerrada", "message": msg}), 400
        flash(msg, "warning")
        return render_template("vote.html", candidates=candidates, event=event, current_event=event, error_message=msg), 400

    # Extract voter email and candidate choice or ratings
    voter_email = ""
    candidate_id = None
    ratings_payload = None

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        voter_email = str(payload.get("voter_email") or payload.get("email") or "").strip()
        candidate_id = payload.get("selected_candidate") or payload.get("candidate_id") or payload.get("participant_id")
        ratings_payload = payload.get("ratings")
    else:
        voter_email = str(request.form.get("voter_email") or request.form.get("email") or "").strip()
        candidate_id = request.form.get("selected_candidate") or request.form.get("candidate_id") or request.form.get("participant_id")
        ratings_dict = {}
        for key, val in request.form.items():
            if key.startswith("rating_"):
                ratings_dict[key[7:]] = val
        if ratings_dict:
            ratings_payload = ratings_dict

    def vote_error(msg: str, status_code: int = 400):
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"status": "error", "error": "Erro na Votação", "message": msg}), status_code
        flash(msg, "warning")
        return (
            render_template(
                "vote.html",
                candidates=candidates,
                event=event,
                current_event=event,
                voter_email=voter_email,
                selected_candidate=candidate_id,
                error_message=msg,
            ),
            status_code,
        )

    # 1. Institutional Email Requirement & Domain Validation
    if not voter_email:
        return vote_error("O e-mail institucional é obrigatório para votar.", 400)

    clean_email = voter_email.lower().strip()
    if not EMAIL_DOMAIN_REGEX.match(clean_email):
        return vote_error(
            "Votação restrita a colaboradores com e-mail institucional @ints.org.br.",
            400,
        )

    # 2. Duplicate Voter Check for this specific event
    if has_voter_voted(clean_email, event_id=event["id"]):
        return vote_error(
            f"O colaborador '{clean_email}' já registrou seu voto neste evento. Cada colaborador pode votar apenas uma vez por concurso.",
            400,
        )

    # 3. Check for Active Candidates in this event
    if not candidates:
        return vote_error(f"Não há participantes cadastrados para votação no evento '{event['nome']}' no momento.", 400)

    # 4. Check for Candidate Selection or Ratings
    if not candidate_id and not ratings_payload:
        return vote_error("Selecione um candidato para registrar seu voto.", 400)

    # 5. Record Vote in SQLite Atomic Transaction
    try:
        if ratings_payload:
            result = record_votes(clean_email, ratings_payload, event_id=event["id"])
            cand_name = "os candidatos avaliados"
        else:
            result = record_single_vote(clean_email, candidate_id, event_id=event["id"])
            cand_name = result.get("participant_name", "")
    except (InvalidVoterEmailError, VoterAlreadyVotedError, InvalidScoreError, MissingParticipantRatingError) as exc:
        return vote_error(str(exc), 400)
    except (ParticipantNotFoundError, EventNotFoundError, EventInactiveError) as exc:
        return vote_error(str(exc), 400)
    except DatabaseError as exc:
        return vote_error(f"Erro ao processar votação: {str(exc)}", 400)
    except Exception as exc:
        return vote_error(f"Erro inesperado no servidor: {str(exc)}", 500)

    # 6. Response
    cand_name = result.get("participant_name") or cand_name or "Candidato Selecionado"
    if request.is_json or request.headers.get("Accept") == "application/json":
        redir_url = (
            url_for("public.vote_success", slug=event["slug"], candidate=cand_name, email=clean_email)
            if slug
            else url_for("public.vote_success", candidate=cand_name, email=clean_email, evento=event["slug"])
        )
        return (
            jsonify({
                "status": "success",
                "message": f"Voto computado com sucesso para {cand_name}!",
                "voter_email": clean_email,
                "candidate_id": candidate_id,
                "event_id": event["id"],
                "event_slug": event["slug"],
                "redirect_url": redir_url,
            }),
            200,
        )

    if slug:
        return redirect(url_for("public.vote_success", slug=event["slug"], candidate=cand_name, email=clean_email))
    return redirect(url_for("public.vote_success", candidate=cand_name, email=clean_email, evento=event["slug"]))


@public_bp.route("/api/check-voter", methods=["GET"])
@public_bp.route("/e/<slug>/api/check-voter", methods=["GET"])
def check_voter(slug: Optional[str] = None):
    """
    Real-time API endpoint to verify voter institutional email status and previous votes in the event.
    """
    event = resolve_event(slug)
    email = request.args.get("email", "").strip().lower()

    if not email:
        return jsonify({"valid": False, "has_voted": False, "message": "Informe seu e-mail institucional."}), 200

    if not EMAIL_DOMAIN_REGEX.match(email):
        return jsonify({
            "valid": False,
            "has_voted": False,
            "message": "E-mail não autorizado! Deve terminar obrigatoriamente com @ints.org.br."
        }), 200

    already_voted = has_voter_voted(email, event_id=event["id"])
    if already_voted:
        return jsonify({
            "valid": True,
            "has_voted": True,
            "event_id": event["id"],
            "event_name": event["nome"],
            "message": f"Voto já registrado! O colaborador '{email}' já votou no evento '{event['nome']}' e não pode votar novamente."
        }), 200

    return jsonify({
        "valid": True,
        "has_voted": False,
        "event_id": event["id"],
        "event_name": event["nome"],
        "message": f"E-mail institucional reconhecido e apto para votar em {event['nome']}: {email}"
    }), 200


@public_bp.route("/vote/success", methods=["GET"])
@public_bp.route("/e/<slug>/vote/success", methods=["GET"])
def vote_success(slug: Optional[str] = None):
    """
    Dedicated post-voting confirmation screen showing vote receipt
    and strict rule that only 1 vote per collaborator is permitted.
    """
    event = resolve_event(slug)
    candidate_name = request.args.get("candidate", "").strip()
    voter_email = request.args.get("email", "").strip()
    return render_template(
        "vote_success.html",
        candidate_name=candidate_name,
        voter_email=voter_email,
        event=event,
        current_event=event,
    )


@public_bp.route("/results", methods=["GET"])
@public_bp.route("/e/<slug>/results", methods=["GET"])
def results(slug: Optional[str] = None):
    """
    Public leaderboard and Olympic podium results endpoint scoped to an event.
    """
    event = resolve_event(slug)
    leaderboard = get_leaderboard(event_id=event["id"])
    summary = get_voting_summary(event_id=event["id"])
    all_events = list_events()

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({
            "event": event,
            "leaderboard": leaderboard,
            "summary": summary
        }), 200

    # Extract Olympic Podium candidates (only if votes have been cast)
    first_place = None
    second_place = None
    third_place = None

    if leaderboard and summary.get("total_votes", 0) > 0:
        if len(leaderboard) > 0 and leaderboard[0]["total_votos"] > 0:
            first_place = leaderboard[0]
        if len(leaderboard) > 1 and leaderboard[1]["total_votos"] > 0:
            second_place = leaderboard[1]
        if len(leaderboard) > 2 and leaderboard[2]["total_votos"] > 0:
            third_place = leaderboard[2]

    return render_template(
        "results.html",
        leaderboard=leaderboard,
        summary=summary,
        first_place=first_place,
        second_place=second_place,
        third_place=third_place,
        event=event,
        current_event=event,
        all_events=all_events,
    )

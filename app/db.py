"""
app/db.py - SQLite persistence, schema initialization, and transactional queries.
INTS Institutional Voting System.
"""

import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from flask import current_app, g, has_app_context
except ImportError:  # pragma: no cover
    g = None
    current_app = None

    def has_app_context():
        return False

# Base directory is the project root (ints_voting_system)
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "voting.db")

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@ints\.org\.br$", re.IGNORECASE)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome_completo TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    descricao TEXT NOT NULL,
    foto_filename TEXT NOT NULL,
    funcao TEXT DEFAULT '',
    setor TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS voters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voter_id INTEGER NOT NULL,
    participant_id INTEGER NOT NULL,
    nota REAL NOT NULL CHECK(nota >= 0.0 AND nota <= 10.0),
    FOREIGN KEY (voter_id) REFERENCES voters(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
    UNIQUE(voter_id, participant_id)
);

CREATE INDEX IF NOT EXISTS idx_votes_participant ON votes(participant_id);
"""


# ==============================================================================
# Custom Exception Hierarchy
# ==============================================================================

class DatabaseError(Exception):
    """Base exception for database domain errors."""
    pass


class DuplicateParticipantEmailError(DatabaseError):
    """Raised when registering a participant with an existing email."""
    pass


class VoterAlreadyVotedError(DatabaseError):
    """Raised when a voter attempts to vote more than once."""
    pass


class InvalidVoterEmailError(DatabaseError):
    """Raised when a voter email does not meet domain criteria (@ints.org.br)."""
    pass


class InvalidScoreError(DatabaseError):
    """Raised when a submitted score is not numeric or out of bounds (0.0 - 10.0)."""
    pass


class MissingParticipantRatingError(DatabaseError):
    """Raised when the submitted ratings do not cover all active participants."""
    pass


class ParticipantNotFoundError(DatabaseError):
    """Raised when a rating is submitted for an unknown participant."""
    pass


# ==============================================================================
# Connection & Lifecycle Management
# ==============================================================================

def create_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    """
    Creates an SQLite connection configured with WAL mode, foreign keys, and 5s timeout.
    """
    path_str = str(db_path)
    if path_str != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path_str)), exist_ok=True)

    conn = sqlite3.connect(
        path_str,
        timeout=5.0,
        isolation_level=None,  # Autocommit mode enables explicit transaction control (BEGIN IMMEDIATE)
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def get_db(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    Retrieves an active connection from Flask application context or creates a standalone connection.
    """
    if has_app_context():
        if "db" not in g:
            configured_path = current_app.config.get("DATABASE_PATH") or current_app.config.get("DATABASE")
            path = db_path or configured_path or DEFAULT_DB_PATH
            g.db = create_connection(path)
        return g.db

    path = db_path or DEFAULT_DB_PATH
    return create_connection(path)


# Alias for compatibility with external references and test harnesses
get_db_connection = get_db


def close_db(e: Optional[BaseException] = None) -> None:
    """Closes the current request's database connection if open."""
    if has_app_context():
        db = g.pop("db", None)
        if db is not None:
            db.close()


def init_db(db_path: Optional[Union[str, Path]] = None) -> None:
    """Initializes SQLite database schema and indexes idempotently."""
    path = db_path
    if path is None:
        if has_app_context():
            path = current_app.config.get("DATABASE_PATH") or current_app.config.get("DATABASE") or DEFAULT_DB_PATH
        else:
            path = DEFAULT_DB_PATH

    conn = create_connection(path)
    try:
        conn.executescript(SCHEMA_SQL)
        # Migração automática de colunas caso o banco já existisse sem elas
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(participants)").fetchall()]
        if "funcao" not in cols:
            cur.execute("ALTER TABLE participants ADD COLUMN funcao TEXT DEFAULT ''")
        if "setor" not in cols:
            cur.execute("ALTER TABLE participants ADD COLUMN setor TEXT DEFAULT ''")
    finally:
        conn.close()


def init_app(app) -> None:
    """Registers database teardown hooks and ensures DB schema exists."""
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


# ==============================================================================
# Participant Queries & Operations
# ==============================================================================

def add_participant(
    nome_completo: str,
    email: str,
    descricao: str,
    foto_filename: str,
    funcao: str = "",
    setor: str = "",
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Adds a new participant to the database.
    Raises DuplicateParticipantEmailError on unique email collision.
    """
    c = conn or get_db()
    clean_nome = nome_completo.strip()
    clean_email = email.strip().lower()
    clean_desc = descricao.strip()
    clean_funcao = str(funcao or "").strip()
    clean_setor = str(setor or "").strip()

    if not clean_nome or not clean_email or not clean_desc or not foto_filename:
        raise ValueError("Todos os campos do participante são obrigatórios.")

    try:
        cur = c.cursor()
        cur.execute(
            "INSERT INTO participants (nome_completo, email, descricao, foto_filename, funcao, setor) VALUES (?, ?, ?, ?, ?, ?)",
            (clean_nome, clean_email, clean_desc, foto_filename, clean_funcao, clean_setor)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        err_msg = str(e).lower()
        if "participants.email" in err_msg or "unique" in err_msg:
            raise DuplicateParticipantEmailError(f"O e-mail '{clean_email}' já está cadastrado como participante.")
        raise DatabaseError(str(e)) from e


create_participant = add_participant


def list_participants(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Returns all registered participants ordered by full name."""
    c = conn or get_db()
    cur = c.cursor()
    rows = cur.execute(
        "SELECT id, nome_completo, email, descricao, foto_filename, funcao, setor, created_at "
        "FROM participants ORDER BY nome_completo ASC, id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_participant_by_id(participant_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a single participant by ID or None if not found."""
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, nome_completo, email, descricao, foto_filename, funcao, setor, created_at "
        "FROM participants WHERE id = ?",
        (participant_id,)
    ).fetchone()
    return dict(row) if row else None


get_participant = get_participant_by_id


def get_participant_by_email(email: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a participant by email (case-insensitive) or None if not found."""
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, nome_completo, email, descricao, foto_filename, funcao, setor, created_at "
        "FROM participants WHERE email = ? COLLATE NOCASE",
        (email.strip(),)
    ).fetchone()
    return dict(row) if row else None


def delete_participant_by_id(participant_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Deletes participant from database.
    Because PRAGMA foreign_keys = ON, dependent votes cascade delete automatically.
    Returns the deleted participant data (including foto_filename) or None if not found.
    """
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, nome_completo, email, descricao, foto_filename FROM participants WHERE id = ?",
        (participant_id,)
    ).fetchone()
    if not row:
        return None

    data = dict(row)
    cur.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
    # Clean up orphan voters who have no remaining votes after cascade delete
    cur.execute("DELETE FROM voters WHERE id NOT IN (SELECT DISTINCT voter_id FROM votes)")
    return data


delete_participant = delete_participant_by_id


# ==============================================================================
# Voting Queries & Transaction
# ==============================================================================

def has_voter_voted(email: str, conn: Optional[sqlite3.Connection] = None) -> bool:
    """Checks whether an institutional email has already recorded votes."""
    if not email or not isinstance(email, str):
        return False
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id FROM voters WHERE email = ? COLLATE NOCASE",
        (email.strip(),)
    ).fetchone()
    return row is not None


has_voted = has_voter_voted


def record_single_vote(
    voter_email: str,
    participant_id: Union[int, str],
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Records a single vote for an institutional voter choosing one candidate.
    Atomic transaction: validates @ints.org.br domain, blocks duplicates, verifies candidate exists.
    """
    if not voter_email or not isinstance(voter_email, str):
        raise InvalidVoterEmailError("O e-mail do votante é obrigatório.")

    clean_email = voter_email.strip().lower()
    if not EMAIL_REGEX.match(clean_email):
        raise InvalidVoterEmailError("Votação restrita a e-mails institucionais @ints.org.br.")

    try:
        pid = int(participant_id)
    except (ValueError, TypeError):
        raise ParticipantNotFoundError(f"Identificador de candidato inválido: {participant_id}")

    c = conn or get_db()
    c.execute("BEGIN IMMEDIATE")
    try:
        cur = c.cursor()

        # 1. Duplicate voter check inside transaction lock
        cur.execute("SELECT id FROM voters WHERE email = ? COLLATE NOCASE", (clean_email,))
        if cur.fetchone():
            raise VoterAlreadyVotedError(f"O colaborador '{clean_email}' já registrou seu voto.")

        # 2. Check candidate existence
        cur.execute("SELECT id, nome_completo FROM participants WHERE id = ?", (pid,))
        participant = cur.fetchone()
        if not participant:
            raise ParticipantNotFoundError("Candidato selecionado não foi encontrado.")

        # 3. Insert voter record
        cur.execute("INSERT INTO voters (email) VALUES (?)", (clean_email,))
        voter_id = cur.lastrowid

        # 4. Insert single vote record (nota = 1.0)
        cur.execute(
            "INSERT INTO votes (voter_id, participant_id, nota) VALUES (?, ?, 1.0)",
            (voter_id, pid)
        )

        c.execute("COMMIT")
        return {
            "voter_id": voter_id,
            "voter_email": clean_email,
            "participant_id": pid,
            "participant_name": participant["nome_completo"],
            "votes_count": 1
        }
    except Exception:
        c.execute("ROLLBACK")
        raise


def record_votes(
    voter_email: str,
    ratings_dict: Union[Dict[Union[int, str], Union[float, int, str]], int, str],
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Records votes for a voter.
    Supports single candidate selection (ratings_dict is int/str) or multi-rating dictionary.
    """
    if isinstance(ratings_dict, (int, str)):
        return record_single_vote(voter_email, ratings_dict, conn)

    if not voter_email or not isinstance(voter_email, str):
        raise InvalidVoterEmailError("O e-mail do votante é obrigatório.")

    clean_email = voter_email.strip().lower()
    if not EMAIL_REGEX.match(clean_email):
        raise InvalidVoterEmailError("Votação restrita a e-mails institucionais @ints.org.br.")

    if not ratings_dict or not isinstance(ratings_dict, dict):
        raise DatabaseError("Nenhuma nota ou candidato fornecido.")

    clean_ratings: Dict[int, float] = {}
    for pid_raw, score_raw in ratings_dict.items():
        try:
            pid = int(pid_raw)
        except (ValueError, TypeError):
            raise ParticipantNotFoundError(f"Identificador de candidato inválido: {pid_raw}")

        if isinstance(score_raw, bool):
            raise InvalidScoreError("Nota não pode ser booleana.")
        try:
            score = float(score_raw)
        except (ValueError, TypeError):
            raise InvalidScoreError(f"A nota para o candidato #{pid} deve ser numérica: {score_raw}")

        if math.isnan(score) or math.isinf(score) or score < 0.0 or score > 10.0:
            raise InvalidScoreError(f"A nota para o candidato #{pid} deve estar entre 0.0 e 10.0: {score}")

        clean_ratings[pid] = score

    c = conn or get_db()
    c.execute("BEGIN IMMEDIATE")
    try:
        cur = c.cursor()
        cur.execute("SELECT id FROM voters WHERE email = ? COLLATE NOCASE", (clean_email,))
        if cur.fetchone():
            raise VoterAlreadyVotedError(f"O colaborador '{clean_email}' já registrou seu voto.")

        active_rows = cur.execute("SELECT id FROM participants").fetchall()
        active_pids = {r["id"] for r in active_rows}

        for pid in clean_ratings.keys():
            if pid not in active_pids:
                raise ParticipantNotFoundError(f"Candidato #{pid} não encontrado no sistema.")

        if len(active_pids) > 1 and len(clean_ratings) < len(active_pids):
            raise MissingParticipantRatingError("Todas as notas dos participantes ativos devem ser preenchidas.")

        cur.execute("INSERT INTO voters (email) VALUES (?)", (clean_email,))
        voter_id = cur.lastrowid

        for pid, score in clean_ratings.items():
            cur.execute(
                "INSERT INTO votes (voter_id, participant_id, nota) VALUES (?, ?, ?)",
                (voter_id, pid, score)
            )

        c.execute("COMMIT")
        return {
            "voter_id": voter_id,
            "voter_email": clean_email,
            "votes_count": len(clean_ratings),
            "ratings": clean_ratings
        }
    except Exception:
        c.execute("ROLLBACK")
        raise


def delete_vote_by_voter_id(voter_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Deletes a voter and their associated votes by voter_id.
    Because PRAGMA foreign_keys = ON, dependent votes cascade delete automatically.
    Returns the deleted voter data or None if not found.
    """
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute("SELECT id, email FROM voters WHERE id = ?", (voter_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    cur.execute("DELETE FROM voters WHERE id = ?", (voter_id,))
    return data


def reset_all_votes(conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Purges all recorded votes and voters from the database.
    Returns the count of purged votes.
    """
    c = conn or get_db()
    cur = c.cursor()
    count = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0] or 0
    cur.execute("DELETE FROM votes")
    cur.execute("DELETE FROM voters")
    return count


# ==============================================================================
# Leaderboard & Audit Reporting
# ==============================================================================

def get_leaderboard(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Returns candidate ranking ordered by total votes received with percentage and Olympic podium metadata.
    """
    c = conn or get_db()
    cur = c.cursor()
    
    total_votes_overall = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0] or 0

    rows = cur.execute("""
        SELECT 
            p.id,
            p.nome_completo,
            p.email,
            p.descricao,
            p.foto_filename,
            p.funcao,
            p.setor,
            p.created_at,
            COUNT(v.id) AS total_votos,
            COALESCE(ROUND(AVG(v.nota), 2), 0.0) AS media_nota
        FROM participants p
        LEFT JOIN votes v ON p.id = v.participant_id
        GROUP BY p.id
        ORDER BY 
            total_votos DESC,
            media_nota DESC,
            p.nome_completo ASC,
            p.id ASC
    """).fetchall()

    leaderboard = []
    for rank, row in enumerate(rows, start=1):
        item = dict(row)
        item["posicao"] = rank
        total_v = item["total_votos"]
        item["percentual"] = round((total_v * 100.0 / total_votes_overall), 1) if total_votes_overall > 0 else 0.0
        if rank == 1 and total_v > 0:
            item["podio"] = "ouro"
        elif rank == 2 and total_v > 0:
            item["podio"] = "prata"
        elif rank == 3 and total_v > 0:
            item["podio"] = "bronze"
        else:
            item["podio"] = None
        leaderboard.append(item)
    return leaderboard


def get_voters_audit(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Returns complete list of institutional voters with their chosen candidate and ratings.
    """
    c = conn or get_db()
    cur = c.cursor()
    voter_rows = cur.execute("""
        SELECT id, email, voted_at
        FROM voters
        ORDER BY voted_at DESC, id DESC
    """).fetchall()

    audit_list = []
    for v in voter_rows:
        voter_id = v["id"]
        voter_email = v["email"]
        voted_at = v["voted_at"]

        votes_rows = cur.execute("""
            SELECT 
                vt.id AS vote_id,
                vt.participant_id,
                vt.nota,
                COALESCE(p.nome_completo, 'Candidato Desclassificado/Excluído') AS participant_name
            FROM votes vt
            LEFT JOIN participants p ON vt.participant_id = p.id
            WHERE vt.voter_id = ?
            ORDER BY vt.id ASC
        """, (voter_id,)).fetchall()

        ratings = [dict(r) for r in votes_rows]
        first_part_name = ratings[0]["participant_name"] if ratings else "Nenhum voto"
        first_part_id = ratings[0]["participant_id"] if ratings else None
        first_nota = ratings[0]["nota"] if ratings else 0.0

        audit_list.append({
            "id": voter_id,
            "voter_id": voter_id,
            "email": voter_email,
            "voter_email": voter_email,
            "voted_at": voted_at,
            "participant_id": first_part_id,
            "participant_name": first_part_name,
            "nota": first_nota,
            "ratings": ratings,
        })

    return audit_list


def get_voting_summary(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Returns global metrics for the administrative dashboard."""
    c = conn or get_db()
    cur = c.cursor()
    total_participants = cur.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    total_voters = cur.execute("SELECT COUNT(*) FROM voters").fetchone()[0]
    total_votes = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    overall_avg = cur.execute("SELECT COALESCE(ROUND(AVG(nota), 2), 0.0) FROM votes").fetchone()[0]
    return {
        "total_participants": total_participants,
        "total_voters": total_voters,
        "total_votes": total_votes,
        "overall_average": overall_avg
    }

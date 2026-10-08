"""
app/db.py - SQLite persistence, schema initialization, and transactional queries.
Sistema de Votação Institucional INTS com suporte a múltiplos eventos simultâneos.
"""

import json
import math
import os
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from flask import current_app, g, has_app_context
except ImportError:  # pragma: no cover
    g = None
    current_app = None

    def has_app_context():
        return False

# Base directory is the project root (sistema-votacao-ints)
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "voting.db")

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@ints\.org\.br$", re.IGNORECASE)


def slugify(text: str) -> str:
    """Converte um nome ou texto em um identificador (slug) limpo e amigável para URLs."""
    if not text:
        return "evento"
    norm = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^\w\s-]", "", norm.lower()).strip()
    slug = re.sub(r"[-\s]+", "-", clean)
    return slug or "evento"


AVAILABLE_THEMES: Dict[str, Dict[str, Any]] = {
    "dracula": {
        "id": "dracula",
        "nome": "Drácula Noturno",
        "subtitulo": "Halloween & Noturno",
        "descricao": "Tons góticos refinados: roxo vampiro, rosa choque, laranja místico e fundo escuro.",
        "primary": "#bd93f9",
        "secondary": "#ff79c6",
        "accent": "#ffb86c",
        "success": "#50fa7b",
        "cyan": "#8be9fd",
        "bg": "#282a36",
        "card": "#343746",
    },
    "carnaval": {
        "id": "carnaval",
        "nome": "Carnaval Festivo",
        "subtitulo": "Alegria, Folia & Samba",
        "descricao": "Cores festivas de concurso: ouro samba, magenta folia, verde tropical e azul turquesa.",
        "primary": "#facc15",
        "secondary": "#ec4899",
        "accent": "#06b6d4",
        "success": "#10b981",
        "cyan": "#38bdf8",
        "bg": "#150d2a",
        "card": "#221644",
    },
    "institucional": {
        "id": "institucional",
        "nome": "INTS Corporativo",
        "subtitulo": "Azul Petróleo & Ciano",
        "descricao": "Visual institucional e sóbrio: azul naval, ciano cristalino e ardósia corporativa.",
        "primary": "#38bdf8",
        "secondary": "#0284c7",
        "accent": "#f59e0b",
        "success": "#10b981",
        "cyan": "#0ea5e9",
        "bg": "#0b1329",
        "card": "#132040",
    },
    "sunset": {
        "id": "sunset",
        "nome": "Pôr do Sol Dourado",
        "subtitulo": "Warm Sunset & Âmbar",
        "descricao": "Cálido para concurso de fotos: dourado âmbar, coral pôr do sol e terracota.",
        "primary": "#f59e0b",
        "secondary": "#f97316",
        "accent": "#fb7185",
        "success": "#10b981",
        "cyan": "#fdba74",
        "bg": "#1c1917",
        "card": "#292524",
    },
    "esmeralda": {
        "id": "esmeralda",
        "nome": "Esmeralda & Natureza",
        "subtitulo": "Verde Floresta & Menta",
        "descricao": "Tons botânicos e frescos: verde esmeralda, menta luminosa e toques dourados.",
        "primary": "#10b981",
        "secondary": "#34d399",
        "accent": "#fbbf24",
        "success": "#22c55e",
        "cyan": "#2dd4bf",
        "bg": "#061a14",
        "card": "#0d2b22",
    },
}
DEFAULT_THEME = "dracula"


def parse_custom_fields(fields_input: Any) -> List[Dict[str, Any]]:
    """
    Safely deserializes and sanitizes custom field definitions for an event.
    Returns a list of dicts with: id, label, tipo, obrigatorio, placeholder, opcoes.
    """
    if not fields_input:
        return []

    parsed = []
    if isinstance(fields_input, str):
        fields_input = fields_input.strip()
        if not fields_input or fields_input in ("[]", "{}"):
            return []
        try:
            parsed = json.loads(fields_input)
        except Exception:
            return []
    elif isinstance(fields_input, list):
        parsed = fields_input
    elif isinstance(fields_input, dict):
        parsed = [fields_input]
    else:
        return []

    if not isinstance(parsed, list):
        return []

    valid_fields: List[Dict[str, Any]] = []
    seen_ids = set()

    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or item.get("nome") or item.get("rotulo") or "").strip()
        if not label:
            continue

        raw_id = str(item.get("id") or item.get("name") or "").strip()
        if not raw_id:
            raw_id = slugify(label).replace("-", "_")
        if not raw_id:
            raw_id = f"campo_{idx}"

        unique_id = raw_id
        counter = 1
        while unique_id in seen_ids:
            unique_id = f"{raw_id}_{counter}"
            counter += 1
        seen_ids.add(unique_id)

        tipo = str(item.get("tipo") or item.get("type") or "text").strip().lower()
        if tipo not in ("text", "textarea", "number", "select"):
            tipo = "text"

        obrigatorio = bool(item.get("obrigatorio") or item.get("required") in (True, 1, "1", "true", "True"))
        placeholder = str(item.get("placeholder") or "").strip()

        opcoes: List[str] = []
        raw_opcoes = item.get("opcoes") or item.get("options")
        if isinstance(raw_opcoes, list):
            opcoes = [str(o).strip() for o in raw_opcoes if str(o).strip()]
        elif isinstance(raw_opcoes, str) and raw_opcoes.strip():
            opcoes = [o.strip() for o in raw_opcoes.split(",") if o.strip()]

        valid_fields.append({
            "id": unique_id,
            "label": label,
            "tipo": tipo,
            "obrigatorio": obrigatorio,
            "placeholder": placeholder,
            "opcoes": opcoes,
        })

    return valid_fields


def parse_custom_data(data_input: Any) -> Dict[str, Any]:
    """
    Safely deserializes and cleans participant custom field responses.
    """
    if not data_input:
        return {}
    if isinstance(data_input, dict):
        return {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in data_input.items() if v is not None}
    if isinstance(data_input, str):
        data_input = data_input.strip()
        if not data_input or data_input in ("{}", "[]"):
            return {}
        try:
            loaded = json.loads(data_input)
            if isinstance(loaded, dict):
                return {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in loaded.items() if v is not None}
        except Exception:
            return {}
    return {}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL COLLATE NOCASE UNIQUE,
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    tema TEXT DEFAULT 'dracula',
    campos_personalizados TEXT DEFAULT '[]',
    ativo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 1,
    nome_completo TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE,
    descricao TEXT NOT NULL,
    foto_filename TEXT NOT NULL,
    funcao TEXT DEFAULT '',
    setor TEXT DEFAULT '',
    dados_personalizados TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    UNIQUE(event_id, email)
);

CREATE TABLE IF NOT EXISTS voters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 1,
    email TEXT NOT NULL COLLATE NOCASE,
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    UNIQUE(event_id, email)
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 1,
    voter_id INTEGER NOT NULL,
    participant_id INTEGER NOT NULL,
    nota REAL NOT NULL CHECK(nota >= 0.0 AND nota <= 10.0),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY (voter_id) REFERENCES voters(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
    UNIQUE(voter_id, participant_id)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    nome TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    avatar_filename TEXT DEFAULT 'avatar_default.jpg',
    is_admin INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_participants_event ON participants(event_id);
CREATE INDEX IF NOT EXISTS idx_voters_event ON voters(event_id);
CREATE INDEX IF NOT EXISTS idx_votes_event ON votes(event_id);
CREATE INDEX IF NOT EXISTS idx_votes_participant ON votes(participant_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""


# ==============================================================================
# Custom Exception Hierarchy
# ==============================================================================

class DatabaseError(Exception):
    """Base exception for database domain errors."""
    pass


class DuplicateUsernameError(DatabaseError):
    """Raised when creating a user with a username that already exists."""
    pass


class UserNotFoundError(DatabaseError):
    """Raised when a user is not found."""
    pass


class EventNotFoundError(DatabaseError):
    """Raised when an operation targets a non-existent voting event."""
    pass


class EventInactiveError(DatabaseError):
    """Raised when attempting to vote or register in an event that is closed/paused."""
    pass


class DuplicateEventSlugError(DatabaseError):
    """Raised when creating an event with an existing slug."""
    pass


class DuplicateParticipantEmailError(DatabaseError):
    """Raised when registering a participant with an existing email in the same event."""
    pass


class VoterAlreadyVotedError(DatabaseError):
    """Raised when a voter attempts to vote more than once in the same event."""
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


get_db_connection = get_db


def close_db(e: Optional[BaseException] = None) -> None:
    """Closes the current request's database connection if open."""
    if has_app_context():
        db = g.pop("db", None)
        if db is not None:
            db.close()


def init_db(db_path: Optional[Union[str, Path]] = None) -> None:
    """
    Initializes SQLite database schema, creates the default event,
    and idempotently runs structural migrations on legacy databases.
    """
    path = db_path
    if path is None:
        if has_app_context():
            path = current_app.config.get("DATABASE_PATH") or current_app.config.get("DATABASE") or DEFAULT_DB_PATH
        else:
            path = DEFAULT_DB_PATH

    conn = create_connection(path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = OFF;")

        # 1. Create events table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL COLLATE NOCASE UNIQUE,
                nome TEXT NOT NULL,
                descricao TEXT DEFAULT '',
                tema TEXT DEFAULT 'dracula',
                campos_personalizados TEXT DEFAULT '[]',
                ativo INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migration: ensure tema & campos_personalizados columns exist in events table
        event_cols = [r[1] for r in cur.execute("PRAGMA table_info(events)").fetchall()]
        if "tema" not in event_cols:
            cur.execute("ALTER TABLE events ADD COLUMN tema TEXT DEFAULT 'dracula'")
        if "campos_personalizados" not in event_cols:
            cur.execute("ALTER TABLE events ADD COLUMN campos_personalizados TEXT DEFAULT '[]'")

        # Ensure default Halloween event exists (id=1, slug='halloween')
        cur.execute("SELECT id FROM events WHERE id = 1 OR slug = 'halloween'")
        if not cur.fetchone():
            cur.execute("""
                INSERT OR IGNORE INTO events (id, slug, nome, descricao, tema, campos_personalizados, ativo)
                VALUES (1, 'halloween', 'Concurso de Fantasias de Halloween', 'Concurso oficial de fantasias de Halloween do INTS', 'dracula', '[]', 1)
            """)

        # 2. Check and migrate participants table
        part_table = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='participants'").fetchone()
        if not part_table:
            cur.execute("""
                CREATE TABLE participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL DEFAULT 1,
                    nome_completo TEXT NOT NULL,
                    email TEXT NOT NULL COLLATE NOCASE,
                    descricao TEXT NOT NULL,
                    foto_filename TEXT NOT NULL,
                    funcao TEXT DEFAULT '',
                    setor TEXT DEFAULT '',
                    dados_personalizados TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
                    UNIQUE(event_id, email)
                );
            """)
        else:
            cols = [r[1] for r in cur.execute("PRAGMA table_info(participants)").fetchall()]
            if "event_id" not in cols:
                cur.execute("ALTER TABLE participants ADD COLUMN event_id INTEGER NOT NULL DEFAULT 1")
            if "funcao" not in cols:
                cur.execute("ALTER TABLE participants ADD COLUMN funcao TEXT DEFAULT ''")
            if "setor" not in cols:
                cur.execute("ALTER TABLE participants ADD COLUMN setor TEXT DEFAULT ''")
            if "dados_personalizados" not in cols:
                cur.execute("ALTER TABLE participants ADD COLUMN dados_personalizados TEXT DEFAULT '{}'")

        # 3. Check and migrate voters table
        voter_table = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voters'").fetchone()
        if not voter_table:
            cur.execute("""
                CREATE TABLE voters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL DEFAULT 1,
                    email TEXT NOT NULL COLLATE NOCASE,
                    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
                    UNIQUE(event_id, email)
                );
            """)
        else:
            cols = [r[1] for r in cur.execute("PRAGMA table_info(voters)").fetchall()]
            if "event_id" not in cols:
                cur.execute("ALTER TABLE voters RENAME TO _voters_old")
                cur.execute("""
                    CREATE TABLE voters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id INTEGER NOT NULL DEFAULT 1,
                        email TEXT NOT NULL COLLATE NOCASE,
                        voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
                        UNIQUE(event_id, email)
                    );
                """)
                cur.execute("""
                    INSERT INTO voters (id, event_id, email, voted_at)
                    SELECT id, 1, email, voted_at FROM _voters_old
                """)
                cur.execute("DROP TABLE _voters_old")

        # 4. Check and migrate votes table
        votes_table = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='votes'").fetchone()
        if not votes_table:
            cur.execute("""
                CREATE TABLE votes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL DEFAULT 1,
                    voter_id INTEGER NOT NULL,
                    participant_id INTEGER NOT NULL,
                    nota REAL NOT NULL CHECK(nota >= 0.0 AND nota <= 10.0),
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
                    FOREIGN KEY (voter_id) REFERENCES voters(id) ON DELETE CASCADE,
                    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                    UNIQUE(voter_id, participant_id)
                );
            """)
        else:
            votes_sql = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='votes'").fetchone()
            cols = [r[1] for r in cur.execute("PRAGMA table_info(votes)").fetchall()]
            if votes_sql and ("_voters_old" in votes_sql[0] or "event_id" not in cols):
                cur.execute("ALTER TABLE votes RENAME TO _votes_old")
                cur.execute("""
                    CREATE TABLE votes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id INTEGER NOT NULL DEFAULT 1,
                        voter_id INTEGER NOT NULL,
                        participant_id INTEGER NOT NULL,
                        nota REAL NOT NULL CHECK(nota >= 0.0 AND nota <= 10.0),
                        FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
                        FOREIGN KEY (voter_id) REFERENCES voters(id) ON DELETE CASCADE,
                        FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                        UNIQUE(voter_id, participant_id)
                    );
                """)
                cur.execute("""
                    INSERT INTO votes (id, event_id, voter_id, participant_id, nota)
                    SELECT id, 1, voter_id, participant_id, nota FROM _votes_old
                """)
                cur.execute("DROP TABLE _votes_old")

        # 5. Check and create users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                nome TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                avatar_filename TEXT DEFAULT 'avatar_default.jpg',
                is_admin INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migration: ensure users table columns exist
        user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
        if "avatar_filename" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN avatar_filename TEXT DEFAULT 'avatar_default.jpg'")
        if "must_change_password" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
        if "is_admin" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 1")
        if "nome" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN nome TEXT DEFAULT ''")

        # Seed initial user 'bruno' with initial password 'trocar'
        cur.execute("SELECT id FROM users WHERE username = 'bruno'")
        if not cur.fetchone():
            from werkzeug.security import generate_password_hash
            pwd_hash = generate_password_hash("trocar")
            cur.execute("""
                INSERT INTO users (username, nome, password_hash, avatar_filename, is_admin, must_change_password)
                VALUES ('bruno', 'Bruno', ?, '', 1, 1)
            """, (pwd_hash,))

        # Delete user 'amanda' if present (user requested removal)
        cur.execute("DELETE FROM users WHERE username = 'amanda'")

        # 6. Create performance indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_participants_event ON participants(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_voters_event ON voters(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_votes_event ON votes(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_votes_participant ON votes(participant_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")

        # 7. Historico de exclusoes de votos (gravado pelo proprio banco, qualquer que seja a origem)
        for stmt in VOTE_DELETIONS_SQL:
            cur.execute(stmt)

        cur.execute("PRAGMA foreign_keys = ON;")
    finally:
        conn.close()


VOTE_DELETIONS_SQL = [
    """
    CREATE TABLE IF NOT EXISTS vote_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deleted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        voter_id INTEGER,
        event_id INTEGER,
        event_nome TEXT,
        email TEXT,
        voted_at TIMESTAMP,
        candidatos TEXT,
        origem TEXT,
        moderador TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_vote_deletions_event ON vote_deletions(event_id, deleted_at)",
    # Eleitor apagado: grava o eleitor e o(s) candidato(s) antes que o voto caia em cascata.
    """
    CREATE TRIGGER IF NOT EXISTS trg_voters_audit_delete BEFORE DELETE ON voters
    BEGIN
        INSERT INTO vote_deletions (voter_id, event_id, event_nome, email, voted_at, candidatos)
        SELECT OLD.id, OLD.event_id,
               (SELECT nome FROM events WHERE id = OLD.event_id),
               OLD.email, OLD.voted_at,
               (SELECT group_concat(p.nome_completo, ' | ')
                  FROM votes v LEFT JOIN participants p ON p.id = v.participant_id
                 WHERE v.voter_id = OLD.id)
        WHERE NOT EXISTS (SELECT 1 FROM vote_deletions WHERE voter_id = OLD.id);
    END
    """,
    # Voto apagado com o eleitor ainda presente (zerar votos, candidato removido, banco direto).
    """
    CREATE TRIGGER IF NOT EXISTS trg_votes_audit_delete BEFORE DELETE ON votes
    WHEN EXISTS (SELECT 1 FROM voters WHERE id = OLD.voter_id)
     AND EXISTS (SELECT 1 FROM participants WHERE id = OLD.participant_id)
     AND EXISTS (SELECT 1 FROM events WHERE id = OLD.event_id)
    BEGIN
        INSERT INTO vote_deletions (voter_id, event_id, event_nome, email, voted_at, candidatos)
        SELECT OLD.voter_id, OLD.event_id,
               (SELECT nome FROM events WHERE id = OLD.event_id),
               vt.email, vt.voted_at,
               (SELECT nome_completo FROM participants WHERE id = OLD.participant_id)
          FROM voters vt WHERE vt.id = OLD.voter_id;
    END
    """,
    # Evento apagado: grava todos os eleitores dele antes da cascata.
    """
    CREATE TRIGGER IF NOT EXISTS trg_events_audit_delete BEFORE DELETE ON events
    BEGIN
        INSERT INTO vote_deletions (voter_id, event_id, event_nome, email, voted_at, candidatos)
        SELECT vt.id, OLD.id, OLD.nome, vt.email, vt.voted_at,
               (SELECT group_concat(p.nome_completo, ' | ')
                  FROM votes v LEFT JOIN participants p ON p.id = v.participant_id
                 WHERE v.voter_id = vt.id)
          FROM voters vt
         WHERE vt.event_id = OLD.id
           AND NOT EXISTS (SELECT 1 FROM vote_deletions WHERE voter_id = vt.id);
    END
    """,
    # Candidato apagado: grava os votos que ele tinha antes que caiam em cascata.
    """
    CREATE TRIGGER IF NOT EXISTS trg_participants_audit_delete BEFORE DELETE ON participants
    BEGIN
        INSERT INTO vote_deletions (voter_id, event_id, event_nome, email, voted_at, candidatos)
        SELECT v.voter_id, v.event_id,
               (SELECT nome FROM events WHERE id = v.event_id),
               vt.email, vt.voted_at, OLD.nome_completo
          FROM votes v JOIN voters vt ON vt.id = v.voter_id
         WHERE v.participant_id = OLD.id
           AND NOT EXISTS (SELECT 1 FROM vote_deletions WHERE voter_id = v.voter_id);
    END
    """,
]


def last_vote_deletion_id(conn: Optional[sqlite3.Connection] = None) -> int:
    c = conn or get_db()
    return c.execute("SELECT COALESCE(MAX(id), 0) FROM vote_deletions").fetchone()[0]


def tag_vote_deletions(since_id: int, origem: str, moderador: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Marca quem e por qual tela apagou os votos gravados depois de since_id."""
    c = conn or get_db()
    c.execute(
        "UPDATE vote_deletions SET origem = ?, moderador = ? WHERE id > ? AND origem IS NULL",
        (origem, moderador, since_id),
    )


def list_vote_deletions(event_id: Optional[int] = None, limit: int = 500, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    c = conn or get_db()
    sql = ("SELECT id, deleted_at, voter_id, event_id, event_nome, email, voted_at, candidatos, "
           "COALESCE(origem, 'direto no banco') AS origem, COALESCE(moderador, '-') AS moderador FROM vote_deletions")
    args: list = []
    if event_id is not None:
        sql += " WHERE event_id = ?"
        args.append(event_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def init_app(app) -> None:
    """Registers database teardown hooks and ensures DB schema exists."""
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


# ==============================================================================
# Event Management Operations
# ==============================================================================

def create_event(
    nome: str,
    slug: Optional[str] = None,
    descricao: str = "",
    tema: str = "dracula",
    ativo: int = 1,
    campos_personalizados: Optional[Union[str, List[Dict[str, Any]]]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Creates a new distinct voting event.
    Auto-generates a clean URL slug if not provided, ensuring uniqueness.
    """
    clean_nome = str(nome or "").strip()
    if not clean_nome:
        raise ValueError("O nome do evento é obrigatório.")

    clean_tema = str(tema or "").strip().lower()
    if clean_tema not in AVAILABLE_THEMES:
        clean_tema = DEFAULT_THEME

    fields_list = parse_custom_fields(campos_personalizados)
    fields_json = json.dumps(fields_list, ensure_ascii=False)

    c = conn or get_db()
    cur = c.cursor()

    base_slug = slugify(slug if slug and str(slug).strip() else clean_nome)
    target_slug = base_slug
    counter = 1

    while True:
        existing = cur.execute("SELECT id FROM events WHERE slug = ? COLLATE NOCASE", (target_slug,)).fetchone()
        if not existing:
            break
        if slug and str(slug).strip():
            raise DuplicateEventSlugError(f"O identificador de URL '{slug}' já está em uso por outro evento.")
        counter += 1
        target_slug = f"{base_slug}-{counter}"

    clean_desc = str(descricao or "").strip()
    is_active = 1 if ativo else 0

    cur.execute(
        "INSERT INTO events (slug, nome, descricao, tema, ativo, campos_personalizados) VALUES (?, ?, ?, ?, ?, ?)",
        (target_slug, clean_nome, clean_desc, clean_tema, is_active, fields_json)
    )
    event_id = cur.lastrowid

    row = cur.execute(
        "SELECT id, slug, nome, descricao, tema, ativo, campos_personalizados, created_at FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()
    res = dict(row)
    res["campos_personalizados_parsed"] = parse_custom_fields(res.get("campos_personalizados"))
    return res


def list_events(ativo_only: bool = False, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Returns all registered voting events along with aggregated statistics
    (total participants, voters, and votes cast).
    """
    c = conn or get_db()
    cur = c.cursor()

    where_clause = "WHERE e.ativo = 1" if ativo_only else ""
    sql = f"""
        SELECT 
            e.id, 
            e.slug, 
            e.nome, 
            e.descricao, 
            e.tema, 
            e.campos_personalizados,
            e.ativo, 
            e.created_at,
            COUNT(DISTINCT p.id) AS total_candidatos,
            COUNT(DISTINCT v.id) AS total_votantes,
            COUNT(DISTINCT vt.id) AS total_votos
        FROM events e
        LEFT JOIN participants p ON p.event_id = e.id
        LEFT JOIN voters v ON v.event_id = e.id
        LEFT JOIN votes vt ON vt.event_id = e.id
        {where_clause}
        GROUP BY e.id
        ORDER BY e.ativo DESC, e.created_at DESC, e.id DESC
    """
    rows = cur.execute(sql).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
        result.append(d)
    return result


def get_event_by_id(event_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves an event by its primary key ID."""
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
    return d


def get_event_by_slug(slug: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves an event by its URL slug (case-insensitive)."""
    if not slug or not isinstance(slug, str):
        return None
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE slug = ? COLLATE NOCASE",
        (slug.strip(),)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
    return d


def get_default_event(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Returns the primary active event.
    Prefers event 1 (Halloween), then the earliest active event, or any event if none active.
    Creates Halloween event if database is completely empty.
    """
    c = conn or get_db()
    cur = c.cursor()

    row = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE id = 1 AND ativo = 1").fetchone()
    if row:
        d = dict(row)
        d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
        return d

    row = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE ativo = 1 ORDER BY id ASC LIMIT 1").fetchone()
    if row:
        d = dict(row)
        d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
        return d

    row = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events ORDER BY id ASC LIMIT 1").fetchone()
    if row:
        d = dict(row)
        d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
        return d

    cur.execute("""
        INSERT OR IGNORE INTO events (id, slug, nome, descricao, tema, campos_personalizados, ativo)
        VALUES (1, 'halloween', 'Concurso de Fantasias de Halloween', 'Concurso oficial de fantasias de Halloween do INTS', 'dracula', '[]', 1)
    """)
    row = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE id = 1").fetchone()
    d = dict(row)
    d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
    return d


def update_event(
    event_id: int,
    nome: Optional[str] = None,
    slug: Optional[str] = None,
    descricao: Optional[str] = None,
    tema: Optional[str] = None,
    ativo: Optional[int] = None,
    campos_personalizados: Optional[Union[str, List[Dict[str, Any]]]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """Updates attributes of an existing event."""
    c = conn or get_db()
    cur = c.cursor()

    existing = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo FROM events WHERE id = ?", (event_id,)).fetchone()
    if not existing:
        return None

    new_nome = nome.strip() if nome is not None else existing["nome"]
    new_desc = descricao.strip() if descricao is not None else existing["descricao"]
    new_ativo = 1 if ativo else 0 if ativo is not None else existing["ativo"]
    if tema is not None:
        new_tema = str(tema).strip().lower()
        if new_tema not in AVAILABLE_THEMES:
            new_tema = DEFAULT_THEME
    else:
        new_tema = existing["tema"] if "tema" in existing.keys() and existing["tema"] else DEFAULT_THEME

    if campos_personalizados is not None:
        new_fields = json.dumps(parse_custom_fields(campos_personalizados), ensure_ascii=False)
    else:
        new_fields = existing["campos_personalizados"] if "campos_personalizados" in existing.keys() and existing["campos_personalizados"] else "[]"

    if slug is not None and slug.strip():
        new_slug = slugify(slug)
        slug_owner = cur.execute("SELECT id FROM events WHERE slug = ? COLLATE NOCASE AND id != ?", (new_slug, event_id)).fetchone()
        if slug_owner:
            raise DuplicateEventSlugError(f"O identificador de URL '{new_slug}' já pertence a outro evento.")
    else:
        new_slug = existing["slug"]

    cur.execute(
        "UPDATE events SET nome = ?, slug = ?, descricao = ?, tema = ?, ativo = ?, campos_personalizados = ? WHERE id = ?",
        (new_nome, new_slug, new_desc, new_tema, new_ativo, new_fields, event_id)
    )

    row = cur.execute("SELECT id, slug, nome, descricao, tema, campos_personalizados, ativo, created_at FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["campos_personalizados_parsed"] = parse_custom_fields(d.get("campos_personalizados"))
    return d


def delete_event_by_id(event_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Deletes an event and cascade-deletes all associated participants, voters, and votes.
    Returns the deleted event data with a list of photo filenames to unlink.
    """
    c = conn or get_db()
    cur = c.cursor()

    row = cur.execute("SELECT id, slug, nome FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    data = dict(row)
    photos = [r["foto_filename"] for r in cur.execute("SELECT foto_filename FROM participants WHERE event_id = ?", (event_id,)).fetchall()]
    data["photos_to_unlink"] = photos

    cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
    return data


# ==============================================================================
# Participant Queries & Operations (Scoped to Event)
# ==============================================================================

def add_participant(
    nome_completo: str = "",
    email: str = "",
    descricao: str = "",
    foto_filename: str = "",
    funcao: str = "",
    setor: str = "",
    dados_personalizados: Optional[Union[str, Dict[str, Any]]] = None,
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Adds a new participant to a specific voting event.
    All fields are optional.
    Raises DuplicateParticipantEmailError on unique email collision within the same event.
    """
    c = conn or get_db()
    cur = c.cursor()

    target_event_id = event_id if event_id is not None else get_default_event(c)["id"]

    event = cur.execute("SELECT id, ativo, nome FROM events WHERE id = ?", (target_event_id,)).fetchone()
    if not event:
        raise EventNotFoundError(f"Evento #{target_event_id} não encontrado.")
    if not event["ativo"]:
        raise EventInactiveError(f"As inscrições para o evento '{event['nome']}' estão encerradas.")

    clean_nome = str(nome_completo or "").strip()
    if not clean_nome:
        clean_nome = "Participante"

    clean_email = str(email or "").strip().lower()
    if not clean_email:
        while True:
            candidate_email = f"participante_{uuid.uuid4().hex[:8]}@ints.org.br"
            exists = cur.execute(
                "SELECT id FROM participants WHERE email = ? COLLATE NOCASE AND event_id = ?",
                (candidate_email, target_event_id)
            ).fetchone()
            if not exists:
                clean_email = candidate_email
                break

    clean_desc = str(descricao or "").strip()
    clean_foto = str(foto_filename or "").strip()
    clean_funcao = str(funcao or "").strip()
    clean_setor = str(setor or "").strip()
    clean_custom = parse_custom_data(dados_personalizados)
    custom_json = json.dumps(clean_custom, ensure_ascii=False)

    try:
        cur.execute(
            "INSERT INTO participants (event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (target_event_id, clean_nome, clean_email, clean_desc, clean_foto, clean_funcao, clean_setor, custom_json)
        )
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        err_msg = str(e).lower()
        if "participants.email" in err_msg or "unique" in err_msg:
            raise DuplicateParticipantEmailError(f"O e-mail '{clean_email}' já está cadastrado neste evento.")
        raise DatabaseError(str(e)) from e


create_participant = add_participant


def list_participants(
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Returns participants ordered by full name.
    If event_id is provided, filters by that event.
    If event_id is None, defaults to the primary active event.
    If event_id is -1, returns all participants across all events.
    """
    c = conn or get_db()
    cur = c.cursor()

    if event_id == -1:
        rows = cur.execute(
            "SELECT id, event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados, created_at "
            "FROM participants ORDER BY nome_completo ASC, id ASC"
        ).fetchall()
    else:
        target_event_id = event_id if event_id is not None else get_default_event(c)["id"]
        rows = cur.execute(
            "SELECT id, event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados, created_at "
            "FROM participants WHERE event_id = ? ORDER BY nome_completo ASC, id ASC",
            (target_event_id,)
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["dados_personalizados"] = parse_custom_data(d.get("dados_personalizados"))
        result.append(d)
    return result


def get_participant_by_id(participant_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a single participant by ID or None if not found."""
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados, created_at "
        "FROM participants WHERE id = ?",
        (participant_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["dados_personalizados"] = parse_custom_data(d.get("dados_personalizados"))
    return d


get_participant = get_participant_by_id


def get_participant_by_email(
    email: str,
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """Retrieves a participant by email within an event, or None if not found."""
    c = conn or get_db()
    cur = c.cursor()
    clean_email = email.strip()

    if event_id is not None:
        row = cur.execute(
            "SELECT id, event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados, created_at "
            "FROM participants WHERE email = ? COLLATE NOCASE AND event_id = ?",
            (clean_email, event_id)
        ).fetchone()
    else:
        target_event_id = get_default_event(c)["id"]
        row = cur.execute(
            "SELECT id, event_id, nome_completo, email, descricao, foto_filename, funcao, setor, dados_personalizados, created_at "
            "FROM participants WHERE email = ? COLLATE NOCASE AND event_id = ?",
            (clean_email, target_event_id)
        ).fetchone()

    if not row:
        return None
    d = dict(row)
    d["dados_personalizados"] = parse_custom_data(d.get("dados_personalizados"))
    return d


def delete_participant_by_id(participant_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Deletes participant from database.
    Because PRAGMA foreign_keys = ON, dependent votes cascade delete automatically.
    Returns the deleted participant data (including foto_filename) or None if not found.
    """
    c = conn or get_db()
    cur = c.cursor()
    row = cur.execute(
        "SELECT id, event_id, nome_completo, email, descricao, foto_filename FROM participants WHERE id = ?",
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
# Voting Queries & Transaction (Scoped to Event)
# ==============================================================================

def has_voter_voted(
    email: str,
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Checks whether an institutional email has already recorded votes for a specific event."""
    if not email or not isinstance(email, str):
        return False
    c = conn or get_db()
    cur = c.cursor()
    clean_email = email.strip()

    target_event_id = event_id if event_id is not None else get_default_event(c)["id"]

    row = cur.execute(
        "SELECT id FROM voters WHERE email = ? COLLATE NOCASE AND event_id = ?",
        (clean_email, target_event_id)
    ).fetchone()
    return row is not None


has_voted = has_voter_voted


def record_single_vote(
    voter_email: str,
    participant_id: Union[int, str],
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Records a single vote for an institutional voter choosing one candidate in an event.
    Atomic transaction: validates @ints.org.br domain, verifies event is active,
    blocks duplicate voters within the event, and stores vote.
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

        # 1. Check candidate existence and obtain event_id
        cur.execute("SELECT id, nome_completo, event_id FROM participants WHERE id = ?", (pid,))
        participant = cur.fetchone()
        if not participant:
            raise ParticipantNotFoundError("Candidato selecionado não foi encontrado.")

        target_event_id = event_id if event_id is not None else participant["event_id"]
        if participant["event_id"] != target_event_id:
            raise ParticipantNotFoundError("O candidato selecionado não pertence a este evento de votação.")

        # 2. Check if event is active
        cur.execute("SELECT id, nome, ativo FROM events WHERE id = ?", (target_event_id,))
        event = cur.fetchone()
        if not event:
            raise EventNotFoundError(f"Evento #{target_event_id} não encontrado.")
        if not event["ativo"]:
            raise EventInactiveError(f"A votação para o evento '{event['nome']}' está encerrada no momento.")

        # 3. Duplicate voter check inside transaction lock for this event
        cur.execute(
            "SELECT id FROM voters WHERE email = ? COLLATE NOCASE AND event_id = ?",
            (clean_email, target_event_id)
        )
        if cur.fetchone():
            raise VoterAlreadyVotedError(
                f"O colaborador '{clean_email}' já registrou seu voto neste concurso. Permitido apenas 1 voto por colaborador."
            )

        # 4. Insert voter record for this event
        cur.execute("INSERT INTO voters (event_id, email) VALUES (?, ?)", (target_event_id, clean_email))
        voter_id = cur.lastrowid

        # 5. Insert single vote record (nota = 1.0)
        cur.execute(
            "INSERT INTO votes (event_id, voter_id, participant_id, nota) VALUES (?, ?, ?, 1.0)",
            (target_event_id, voter_id, pid)
        )

        c.execute("COMMIT")
        return {
            "voter_id": voter_id,
            "voter_email": clean_email,
            "participant_id": pid,
            "participant_name": participant["nome_completo"],
            "event_id": target_event_id,
            "event_name": event["nome"],
            "votes_count": 1
        }
    except Exception:
        c.execute("ROLLBACK")
        raise


def record_votes(
    voter_email: str,
    ratings_dict: Union[Dict[Union[int, str], Union[float, int, str]], int, str],
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Records votes for a voter in an event.
    Supports single candidate selection (ratings_dict is int/str) or multi-rating dictionary.
    """
    if isinstance(ratings_dict, (int, str)):
        return record_single_vote(voter_email, ratings_dict, event_id, conn)

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

        # Derive event_id from candidates if not explicitly provided
        first_pid = next(iter(clean_ratings.keys()))
        part = cur.execute("SELECT event_id FROM participants WHERE id = ?", (first_pid,)).fetchone()
        if not part:
            raise ParticipantNotFoundError(f"Candidato #{first_pid} não encontrado.")

        target_event_id = event_id if event_id is not None else part["event_id"]

        # Check event active
        cur.execute("SELECT id, nome, ativo FROM events WHERE id = ?", (target_event_id,))
        event = cur.fetchone()
        if not event:
            raise EventNotFoundError(f"Evento #{target_event_id} não encontrado.")
        if not event["ativo"]:
            raise EventInactiveError(f"A votação para o evento '{event['nome']}' está encerrada no momento.")

        cur.execute(
            "SELECT id FROM voters WHERE email = ? COLLATE NOCASE AND event_id = ?",
            (clean_email, target_event_id)
        )
        if cur.fetchone():
            raise VoterAlreadyVotedError(f"O colaborador '{clean_email}' já registrou seu voto neste concurso.")

        active_rows = cur.execute("SELECT id FROM participants WHERE event_id = ?", (target_event_id,)).fetchall()
        active_pids = {r["id"] for r in active_rows}

        for pid in clean_ratings.keys():
            if pid not in active_pids:
                raise ParticipantNotFoundError(f"Candidato #{pid} não encontrado no evento.")

        if len(active_pids) > 1 and len(clean_ratings) < len(active_pids):
            raise MissingParticipantRatingError("Todas as notas dos participantes ativos devem ser preenchidas.")

        cur.execute("INSERT INTO voters (event_id, email) VALUES (?, ?)", (target_event_id, clean_email))
        voter_id = cur.lastrowid

        for pid, score in clean_ratings.items():
            cur.execute(
                "INSERT INTO votes (event_id, voter_id, participant_id, nota) VALUES (?, ?, ?, ?)",
                (target_event_id, voter_id, pid, score)
            )

        c.execute("COMMIT")
        return {
            "voter_id": voter_id,
            "voter_email": clean_email,
            "event_id": target_event_id,
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
    row = cur.execute("SELECT id, event_id, email FROM voters WHERE id = ?", (voter_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    cur.execute("DELETE FROM voters WHERE id = ?", (voter_id,))
    return data


def reset_all_votes(
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Purges recorded votes and voters from the database.
    If event_id is given, purges only votes/voters of that event.
    If event_id is None, purges default event votes.
    If event_id is -1, purges all votes across all events.
    Returns the count of purged votes.
    """
    c = conn or get_db()
    cur = c.cursor()

    if event_id == -1:
        count = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0] or 0
        cur.execute("DELETE FROM votes")
        cur.execute("DELETE FROM voters")
        return count

    target_event_id = event_id if event_id is not None else get_default_event(c)["id"]
    count = cur.execute("SELECT COUNT(*) FROM votes WHERE event_id = ?", (target_event_id,)).fetchone()[0] or 0
    cur.execute("DELETE FROM votes WHERE event_id = ?", (target_event_id,))
    cur.execute("DELETE FROM voters WHERE event_id = ?", (target_event_id,))
    return count


# ==============================================================================
# Leaderboard & Audit Reporting (Scoped to Event)
# ==============================================================================

def get_leaderboard(
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Returns candidate ranking ordered by total votes received with percentage and Olympic podium metadata.
    Scoped to event_id (or default event if None).
    """
    c = conn or get_db()
    cur = c.cursor()

    if event_id == -1:
        total_votes_overall = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0] or 0
        rows = cur.execute("""
            SELECT 
                p.id,
                p.event_id,
                p.nome_completo,
                p.email,
                p.descricao,
                p.foto_filename,
                p.funcao,
                p.setor,
                p.dados_personalizados,
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
    else:
        target_event_id = event_id if event_id is not None else get_default_event(c)["id"]
        total_votes_overall = cur.execute("SELECT COUNT(*) FROM votes WHERE event_id = ?", (target_event_id,)).fetchone()[0] or 0
        rows = cur.execute("""
            SELECT 
                p.id,
                p.event_id,
                p.nome_completo,
                p.email,
                p.descricao,
                p.foto_filename,
                p.funcao,
                p.setor,
                p.dados_personalizados,
                p.created_at,
                COUNT(v.id) AS total_votos,
                COALESCE(ROUND(AVG(v.nota), 2), 0.0) AS media_nota
            FROM participants p
            LEFT JOIN votes v ON p.id = v.participant_id AND v.event_id = p.event_id
            WHERE p.event_id = ?
            GROUP BY p.id
            ORDER BY 
                total_votos DESC,
                media_nota DESC,
                p.nome_completo ASC,
                p.id ASC
        """, (target_event_id,)).fetchall()

    leaderboard = []
    for rank, row in enumerate(rows, start=1):
        item = dict(row)
        item["dados_personalizados"] = parse_custom_data(item.get("dados_personalizados"))
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


def get_voters_audit(
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Returns complete list of institutional voters with their chosen candidate and ratings.
    Scoped to event_id (or default event if None; -1 for all events).
    """
    c = conn or get_db()
    cur = c.cursor()

    if event_id == -1:
        voter_rows = cur.execute("""
            SELECT v.id, v.event_id, v.email, v.voted_at, e.nome AS event_name, e.slug AS event_slug
            FROM voters v
            LEFT JOIN events e ON v.event_id = e.id
            ORDER BY v.voted_at DESC, v.id DESC
        """).fetchall()
    else:
        target_event_id = event_id if event_id is not None else get_default_event(c)["id"]
        voter_rows = cur.execute("""
            SELECT v.id, v.event_id, v.email, v.voted_at, e.nome AS event_name, e.slug AS event_slug
            FROM voters v
            LEFT JOIN events e ON v.event_id = e.id
            WHERE v.event_id = ?
            ORDER BY v.voted_at DESC, v.id DESC
        """, (target_event_id,)).fetchall()

    audit_list = []
    for v in voter_rows:
        voter_id = v["id"]
        voter_email = v["email"]
        voted_at = v["voted_at"]
        ev_id = v["event_id"]
        v_keys = v.keys()
        ev_name = v["event_name"] if "event_name" in v_keys and v["event_name"] else "Evento"

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
            "event_id": ev_id,
            "event_name": ev_name,
            "email": voter_email,
            "voter_email": voter_email,
            "voted_at": voted_at,
            "participant_id": first_part_id,
            "participant_name": first_part_name,
            "nota": first_nota,
            "ratings": ratings,
        })

    return audit_list


def get_voting_summary(
    event_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Returns metrics for the administrative dashboard.
    Scoped to event_id (or default event if None; -1 for all events combined).
    """
    c = conn or get_db()
    cur = c.cursor()

    if event_id == -1:
        total_participants = cur.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        total_voters = cur.execute("SELECT COUNT(*) FROM voters").fetchone()[0]
        total_votes = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        overall_avg = cur.execute("SELECT COALESCE(ROUND(AVG(nota), 2), 0.0) FROM votes").fetchone()[0]
    else:
        target_event_id = event_id if event_id is not None else get_default_event(c)["id"]
        total_participants = cur.execute("SELECT COUNT(*) FROM participants WHERE event_id = ?", (target_event_id,)).fetchone()[0]
        total_voters = cur.execute("SELECT COUNT(*) FROM voters WHERE event_id = ?", (target_event_id,)).fetchone()[0]
        total_votes = cur.execute("SELECT COUNT(*) FROM votes WHERE event_id = ?", (target_event_id,)).fetchone()[0]
        overall_avg = cur.execute(
            "SELECT COALESCE(ROUND(AVG(nota), 2), 0.0) FROM votes WHERE event_id = ?",
            (target_event_id,)
        ).fetchone()[0]

    return {
        "total_participants": total_participants,
        "total_voters": total_voters,
        "total_votes": total_votes,
        "overall_average": overall_avg
    }


# ==============================================================================
# Administrative Users & Authentication Operations
# ==============================================================================

def create_user(
    username: str,
    password: str,
    nome: str = "",
    avatar_filename: Optional[str] = None,
    is_admin: int = 1,
    must_change_password: int = 1,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Creates a new administrative user with hashed password and must_change_password flag.
    All created users have administrator access (is_admin=1).
    """
    clean_username = str(username or "").strip().lower()
    if not clean_username:
        raise ValueError("O nome de usuário não pode ser vazio.")
    if not re.match(r"^[a-zA-Z0-9_.-]{3,50}$", clean_username):
        raise ValueError("O nome de usuário deve conter de 3 a 50 caracteres (letras, números, '.', '-' ou '_').")
    
    clean_password = str(password or "").strip()
    if not clean_password or len(clean_password) < 4:
        raise ValueError("A senha inicial deve conter pelo menos 4 caracteres.")

    chosen_avatar = ""

    from werkzeug.security import generate_password_hash
    pwd_hash = generate_password_hash(clean_password)

    c = conn or get_db()
    cur = c.cursor()
    try:
        cur.execute("""
            INSERT INTO users (username, nome, password_hash, avatar_filename, is_admin, must_change_password)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            clean_username,
            str(nome or "").strip(),
            pwd_hash,
            chosen_avatar,
            1 if is_admin else 0,
            1 if must_change_password else 0
        ))
        user_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e).upper():
            raise DuplicateUsernameError(f"O nome de usuário '{clean_username}' já está em uso.") from e
        raise DatabaseError(f"Erro ao criar usuário: {e}") from e

    return get_user_by_id(user_id, c)


def get_user_by_id(user_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a user by database ID."""
    c = conn or get_db()
    row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a user by username (case-insensitive)."""
    clean = str(username or "").strip().lower()
    c = conn or get_db()
    row = c.execute("SELECT * FROM users WHERE username = ?", (clean,)).fetchone()
    return dict(row) if row else None


def list_users(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Lists all administrative users sorted chronologically."""
    c = conn or get_db()
    rows = c.execute("""
        SELECT id, username, nome, avatar_filename, is_admin, must_change_password, created_at, updated_at
        FROM users
        ORDER BY created_at ASC
    """).fetchall()
    return [dict(r) for r in rows]


def update_user_password(
    user_id: int,
    new_password: str,
    must_change_password: int = 0,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """Updates a user's password hash and resets must_change_password flag."""
    clean_pwd = str(new_password or "").strip()
    if not clean_pwd or len(clean_pwd) < 6:
        raise ValueError("A nova senha deve ter no mínimo 6 caracteres.")
    if clean_pwd.lower() == "trocar":
        raise ValueError("A nova senha não pode ser a senha padrão ('trocar'). Escolha uma senha segura.")

    from werkzeug.security import generate_password_hash
    pwd_hash = generate_password_hash(clean_pwd)

    c = conn or get_db()
    cur = c.cursor()
    cur.execute("""
        UPDATE users 
        SET password_hash = ?, must_change_password = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (pwd_hash, 1 if must_change_password else 0, user_id))
    if cur.rowcount == 0:
        raise UserNotFoundError(f"Usuário id {user_id} não encontrado.")
    return True


def delete_user(user_id: int, conn: Optional[sqlite3.Connection] = None) -> bool:
    """Deletes an administrative user. Prevents deleting the last admin."""
    c = conn or get_db()
    cur = c.cursor()
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if total <= 1:
        raise ValueError("Não é permitido excluir o único usuário administrador do sistema.")
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    if cur.rowcount == 0:
        raise UserNotFoundError(f"Usuário id {user_id} não encontrado.")
    return True


def authenticate_user(
    username: str,
    password: str,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """
    Validates user credentials against hashed password.
    Returns user dict on success, None on failure.
    """
    user = get_user_by_username(username, conn)
    if not user:
        return None
    from werkzeug.security import check_password_hash
    if check_password_hash(user["password_hash"], str(password or "").strip()):
        return user
    return None


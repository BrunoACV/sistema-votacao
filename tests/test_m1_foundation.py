"""
tests/test_m1_foundation.py - Comprehensive Unit & Integration Test Suite for Milestone 1.

Tests:
1. SQLite Database Schema, Pragmas (WAL, foreign_keys=ON, busy_timeout), and Indexes
2. Foreign Key Cascade Deletion on participants and voters
3. Participant CRUD and Case-Insensitive Email Uniqueness (COLLATE NOCASE)
4. Atomic Voting Transaction (BEGIN IMMEDIATE), Domain Enforcement (@ints.org.br), and Bounds Check
5. Reporting queries: Leaderboard (with Olympic podium metadata), Voters Audit, and Summary
6. Photo Validation, Pillow Deep Inspection, and UUID Filename Generation
7. Photo Deletion, Idempotency, and Path Traversal Attack Prevention
8. End-to-End Foundation Integration Lifecycle
9. Configuration, Directory Auto-Healing, and Production Validation Guards

Run via:
    python -m unittest tests/test_m1_foundation.py
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import M1 modules
from app.config import Config, DevelopmentConfig, ProductionConfig, TestingConfig, get_config
from app.db import (
    create_connection,
    get_db,
    get_db_connection,
    init_db,
    add_participant,
    create_participant,
    list_participants,
    get_participant_by_id,
    get_participant,
    get_participant_by_email,
    delete_participant_by_id,
    delete_participant,
    has_voter_voted,
    has_voted,
    record_votes,
    get_leaderboard,
    get_voters_audit,
    get_voting_summary,
    DatabaseError,
    DuplicateParticipantEmailError,
    VoterAlreadyVotedError,
    InvalidVoterEmailError,
    InvalidScoreError,
    MissingParticipantRatingError,
    ParticipantNotFoundError,
    SCHEMA_SQL,
)
from app.storage import (
    save_photo,
    delete_photo,
    get_photo_path,
    validate_photo,
    ensure_upload_dir,
    get_upload_dir,
    StorageError,
    StorageValidationError,
    StorageFileTooLargeError,
    StorageInvalidFormatError,
    InvalidImageError,
)

try:
    from werkzeug.datastructures import FileStorage
except ImportError:
    FileStorage = None


class TestM1Foundation(unittest.TestCase):
    """Milestone 1 Core Infrastructure, Database & Storage Engine Test Suite."""

    def setUp(self):
        """Create isolated sandbox directory for database and uploads."""
        self.test_dir = tempfile.mkdtemp(prefix="ints_test_m1_")
        self.db_path = Path(self.test_dir) / "test_voting.db"
        self.upload_dir = Path(self.test_dir) / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        # Initialize isolated test database
        init_db(self.db_path)

    def tearDown(self):
        """Clean up all sandbox files."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _get_connection(self):
        """Returns a configured connection to the test database."""
        return create_connection(self.db_path)

    def _create_mock_image(self, fmt="JPEG", size=(120, 120), color=(0, 51, 102), filename="photo.jpg"):
        """Generates an in-memory image buffer or FileStorage."""
        img = Image.new("RGB", size, color=color)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        buf.seek(0)

        mime_map = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }
        mime = mime_map.get(fmt.upper(), "image/jpeg")

        if FileStorage:
            return FileStorage(stream=buf, filename=filename, content_type=mime)
        buf.name = filename
        buf.content_type = mime
        return buf

    # ==========================================================================
    # TEST 1: Database Schema, Pragmas, and Indexes
    # ==========================================================================
    def test_01_db_schema_creation_and_pragmas(self):
        """Verify tables creation, indexes, foreign_keys=ON, WAL journal_mode, and busy_timeout."""
        conn = self._get_connection()
        try:
            # 1. Foreign keys enabled
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk, 1, "PRAGMA foreign_keys must be enabled (1).")

            # 2. WAL journal mode
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(jm.lower(), "wal", "PRAGMA journal_mode must be WAL.")

            # 3. Busy timeout
            bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertGreaterEqual(bt, 5000, "PRAGMA busy_timeout must be at least 5000ms.")

            # 4. Tables existence
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()]
            self.assertIn("participants", tables)
            self.assertIn("voters", tables)
            self.assertIn("votes", tables)

            # 5. Index existence
            indexes = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index';"
            ).fetchall()]
            self.assertIn("idx_votes_participant", indexes)
        finally:
            conn.close()

    # ==========================================================================
    # TEST 2: Foreign Key Cascade Deletion
    # ==========================================================================
    def test_02_foreign_key_cascade_deletion(self):
        """Verify that deleting a participant or voter automatically purges related votes."""
        conn = self._get_connection()
        try:
            p1_id = add_participant("Candidato 1", "c1@ints.org.br", "Desc 1", "f1.jpg", conn=conn)
            p2_id = add_participant("Candidato 2", "c2@ints.org.br", "Desc 2", "f2.jpg", conn=conn)

            # Cast votes
            record_votes(
                "eleitor@ints.org.br",
                {p1_id: 9.5, p2_id: 8.0},
                conn=conn
            )

            # Confirm 2 votes exist
            vote_count = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
            self.assertEqual(vote_count, 2)

            # Delete Participant 1
            deleted = delete_participant_by_id(p1_id, conn=conn)
            self.assertIsNotNone(deleted)
            self.assertEqual(deleted["foto_filename"], "f1.jpg")

            # Votes for p1 should be deleted by cascade, but p2 votes remain
            p1_votes = conn.execute("SELECT COUNT(*) FROM votes WHERE participant_id = ?", (p1_id,)).fetchone()[0]
            self.assertEqual(p1_votes, 0, "Votes for deleted participant must be deleted by cascade.")

            p2_votes = conn.execute("SELECT COUNT(*) FROM votes WHERE participant_id = ?", (p2_id,)).fetchone()[0]
            self.assertEqual(p2_votes, 1, "Votes for remaining participant must remain intact.")

            # Delete voter directly
            voter_id = conn.execute("SELECT id FROM voters WHERE email = 'eleitor@ints.org.br'").fetchone()[0]
            conn.execute("DELETE FROM voters WHERE id = ?", (voter_id,))

            total_votes = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
            self.assertEqual(total_votes, 0, "Deleting voter must cascade to remaining votes.")
        finally:
            conn.close()

    # ==========================================================================
    # TEST 3: Participant CRUD and Case-Insensitive Email Uniqueness
    # ==========================================================================
    def test_03_participant_crud_and_case_insensitive_uniqueness(self):
        """Verify participant CRUD and case-insensitive unique constraint on email."""
        conn = self._get_connection()
        try:
            # Create participant
            pid = create_participant("Ana Souza", "ana.souza@ints.org.br", "Fantasia Solar", "ana.jpg", conn=conn)
            self.assertIsInstance(pid, int)
            self.assertGreater(pid, 0)

            # Read by ID
            p = get_participant(pid, conn=conn)
            self.assertIsNotNone(p)
            self.assertEqual(p["nome_completo"], "Ana Souza")
            self.assertEqual(p["email"], "ana.souza@ints.org.br")
            self.assertEqual(p["descricao"], "Fantasia Solar")

            # Read by Email
            p_email = get_participant_by_email("ANA.SOUZA@INTS.ORG.BR", conn=conn)
            self.assertIsNotNone(p_email)
            self.assertEqual(p_email["id"], pid)

            # Duplicate email exact match must raise DuplicateParticipantEmailError
            with self.assertRaises(DuplicateParticipantEmailError):
                add_participant("Clone Ana", "ana.souza@ints.org.br", "Outra", "clone.jpg", conn=conn)

            # Duplicate email with different case must also raise DuplicateParticipantEmailError
            with self.assertRaises(DuplicateParticipantEmailError):
                add_participant("Clone Ana 2", "ANA.SOUZA@INTS.ORG.BR", "Outra 2", "clone2.jpg", conn=conn)

            # List participants
            all_parts = list_participants(conn=conn)
            self.assertEqual(len(all_parts), 1)

            # Delete participant
            del_result = delete_participant(pid, conn=conn)
            self.assertIsNotNone(del_result)
            self.assertIsNone(get_participant(pid, conn=conn))
            self.assertEqual(len(list_participants(conn=conn)), 0)
        finally:
            conn.close()

    # ==========================================================================
    # TEST 4: Atomic Voting Transaction, Domain & Bounds Check
    # ==========================================================================
    def test_04_atomic_voting_transaction_and_validations(self):
        """Verify atomic voting, strict domain validation (@ints.org.br), score bounds, and rollback."""
        conn = self._get_connection()
        try:
            # Register two participants
            p1_id = add_participant("Dr. Carlos", "carlos@ints.org.br", "Desc A", "c.jpg", conn=conn)
            p2_id = add_participant("Dra. Beatriz", "beatriz@ints.org.br", "Desc B", "b.jpg", conn=conn)

            # 1. Non-institutional email domain rejection (@gmail.com, @outlook.com)
            with self.assertRaises(InvalidVoterEmailError):
                record_votes("usuario@gmail.com", {p1_id: 8.0, p2_id: 9.0}, conn=conn)

            with self.assertRaises(InvalidVoterEmailError):
                record_votes("usuario@empresa.com", {p1_id: 8.0, p2_id: 9.0}, conn=conn)

            with self.assertRaises(InvalidVoterEmailError):
                record_votes("", {p1_id: 8.0, p2_id: 9.0}, conn=conn)

            # 2. Missing participant ratings rejection
            with self.assertRaises(MissingParticipantRatingError):
                record_votes("eleitor1@ints.org.br", {p1_id: 8.0}, conn=conn)

            # 3. Non-existent participant ID in ratings
            with self.assertRaises(ParticipantNotFoundError):
                record_votes("eleitor1@ints.org.br", {p1_id: 8.0, p2_id: 9.0, 99999: 5.0}, conn=conn)

            # 4. Out-of-bounds score rejection (> 10.0 or < 0.0)
            with self.assertRaises(InvalidScoreError):
                record_votes("eleitor1@ints.org.br", {p1_id: 10.5, p2_id: 8.0}, conn=conn)

            with self.assertRaises(InvalidScoreError):
                record_votes("eleitor1@ints.org.br", {p1_id: -1.0, p2_id: 8.0}, conn=conn)

            with self.assertRaises(InvalidScoreError):
                record_votes("eleitor1@ints.org.br", {p1_id: "dez", p2_id: 8.0}, conn=conn)

            # 5. Successful vote submission
            self.assertFalse(has_voted("eleitor1@ints.org.br", conn=conn))
            vote_result = record_votes(
                "eleitor1@ints.org.br",
                {p1_id: 10.0, p2_id: 8.0},
                conn=conn
            )
            self.assertEqual(vote_result["voter_email"], "eleitor1@ints.org.br")
            self.assertEqual(vote_result["votes_count"], 2)
            self.assertTrue(has_voted("eleitor1@ints.org.br", conn=conn))
            self.assertTrue(has_voted("ELEITOR1@INTS.ORG.BR", conn=conn))

            # 6. Duplicate vote attempt with exact email must fail
            with self.assertRaises(VoterAlreadyVotedError):
                record_votes("eleitor1@ints.org.br", {p1_id: 9.0, p2_id: 9.0}, conn=conn)

            # 7. Duplicate vote attempt with different case must also fail
            with self.assertRaises(VoterAlreadyVotedError):
                record_votes("ELEITOR1@INTS.ORG.BR", {p1_id: 9.0, p2_id: 9.0}, conn=conn)

            # Verify no partial or corrupt state
            voters_count = conn.execute("SELECT COUNT(*) FROM voters").fetchone()[0]
            self.assertEqual(voters_count, 1)
        finally:
            conn.close()

    # ==========================================================================
    # TEST 5: Reporting Queries (Leaderboard, Audit, Summary)
    # ==========================================================================
    def test_05_reporting_queries(self):
        """Verify leaderboard ranking, Olympic podium metadata, voter audit, and global summary."""
        conn = self._get_connection()
        try:
            # Create 4 candidates
            p1 = add_participant("Alice", "alice@ints.org.br", "Desc A", "a.jpg", conn=conn)
            p2 = add_participant("Bruno", "bruno@ints.org.br", "Desc B", "b.jpg", conn=conn)
            p3 = add_participant("Carla", "carla@ints.org.br", "Desc C", "c.jpg", conn=conn)
            p4 = add_participant("Diego", "diego@ints.org.br", "Desc D", "d.jpg", conn=conn)

            # Voter 1
            record_votes(
                "v1@ints.org.br",
                {p1: 10.0, p2: 9.0, p3: 8.0, p4: 7.0},
                conn=conn
            )
            # Voter 2
            record_votes(
                "v2@ints.org.br",
                {p1: 9.0, p2: 8.0, p3: 7.0, p4: 6.0},
                conn=conn
            )

            # 1. Leaderboard
            board = get_leaderboard(conn=conn)
            self.assertEqual(len(board), 4)

            # Alice should be 1st (avg 9.5, podio ouro)
            self.assertEqual(board[0]["id"], p1)
            self.assertEqual(board[0]["media_nota"], 9.5)
            self.assertEqual(board[0]["posicao"], 1)
            self.assertEqual(board[0]["podio"], "ouro")

            # Bruno should be 2nd (avg 8.5, podio prata)
            self.assertEqual(board[1]["id"], p2)
            self.assertEqual(board[1]["media_nota"], 8.5)
            self.assertEqual(board[1]["posicao"], 2)
            self.assertEqual(board[1]["podio"], "prata")

            # Carla should be 3rd (avg 7.5, podio bronze)
            self.assertEqual(board[2]["id"], p3)
            self.assertEqual(board[2]["media_nota"], 7.5)
            self.assertEqual(board[2]["posicao"], 3)
            self.assertEqual(board[2]["podio"], "bronze")

            # Diego should be 4th (avg 6.5, podio None)
            self.assertEqual(board[3]["id"], p4)
            self.assertEqual(board[3]["media_nota"], 6.5)
            self.assertEqual(board[3]["posicao"], 4)
            self.assertIsNone(board[3]["podio"])

            # 2. Voters Audit
            audit = get_voters_audit(conn=conn)
            self.assertEqual(len(audit), 2)
            voter_emails = {a["email"] for a in audit}
            self.assertIn("v1@ints.org.br", voter_emails)
            self.assertIn("v2@ints.org.br", voter_emails)
            self.assertEqual(len(audit[0]["ratings"]), 4)

            # 3. Summary
            summary = get_voting_summary(conn=conn)
            self.assertEqual(summary["total_participants"], 4)
            self.assertEqual(summary["total_voters"], 2)
            self.assertEqual(summary["total_votes"], 8)
            self.assertGreater(summary["overall_average"], 0)
        finally:
            conn.close()

    # ==========================================================================
    # TEST 6: Photo Validation and Saving Engine
    # ==========================================================================
    def test_06_photo_validation_and_saving(self):
        """Verify image format detection (JPEG, PNG, WEBP), Pillow verification, and secure UUID saving."""
        # 1. Valid JPEG
        jpeg_img = self._create_mock_image(fmt="JPEG", filename="valid.jpg")
        saved_jpeg = save_photo(jpeg_img, upload_folder=self.upload_dir)
        self.assertTrue(saved_jpeg.endswith(".jpg"))
        saved_jpeg_path = self.upload_dir / saved_jpeg
        self.assertTrue(saved_jpeg_path.exists())
        with Image.open(saved_jpeg_path) as im:
            im.verify()

        # 2. Valid PNG
        png_img = self._create_mock_image(fmt="PNG", filename="valid.png")
        saved_png = save_photo(png_img, upload_folder=self.upload_dir)
        self.assertTrue(saved_png.endswith(".png"))
        self.assertTrue((self.upload_dir / saved_png).exists())

        # 3. Valid WEBP
        webp_img = self._create_mock_image(fmt="WEBP", filename="valid.webp")
        saved_webp = save_photo(webp_img, upload_folder=self.upload_dir)
        self.assertTrue(saved_webp.endswith(".webp"))
        self.assertTrue((self.upload_dir / saved_webp).exists())

        # 4. Reject empty file (0 bytes)
        empty_buf = io.BytesIO(b"")
        if FileStorage:
            empty_file = FileStorage(stream=empty_buf, filename="empty.jpg", content_type="image/jpeg")
        else:
            empty_file = empty_buf
        with self.assertRaises(StorageValidationError):
            save_photo(empty_file, upload_folder=self.upload_dir)

        # 5. Reject fake corrupted image (disguised text)
        fake_buf = io.BytesIO(b"Not a real JPEG image at all.")
        if FileStorage:
            fake_file = FileStorage(stream=fake_buf, filename="fake.jpg", content_type="image/jpeg")
        else:
            fake_file = fake_buf
            fake_file.name = "fake.jpg"
        with self.assertRaises(StorageValidationError):
            save_photo(fake_file, upload_folder=self.upload_dir)

        # 6. Reject disallowed extension (.sh, .exe, .html)
        disallowed_buf = self._create_mock_image(fmt="JPEG", filename="malicious.exe")
        with self.assertRaises(StorageInvalidFormatError):
            save_photo(disallowed_buf, upload_folder=self.upload_dir)

        # 7. Reject oversized file
        large_buf = io.BytesIO(b"X" * (11 * 1024 * 1024))
        if FileStorage:
            large_file = FileStorage(stream=large_buf, filename="oversized.jpg", content_type="image/jpeg")
        else:
            large_file = large_buf
            large_file.name = "oversized.jpg"
        with self.assertRaises(StorageFileTooLargeError):
            save_photo(large_file, upload_folder=self.upload_dir, max_size_bytes=10 * 1024 * 1024)

    # ==========================================================================
    # TEST 7: Photo Deletion and Path Traversal Protection
    # ==========================================================================
    def test_07_photo_deletion_and_security(self):
        """Verify file deletion, idempotency with missing files, and directory traversal defense."""
        # 1. Create a dummy photo
        test_filename = "photo_to_delete.jpg"
        photo_path = self.upload_dir / test_filename
        photo_path.write_bytes(b"dummy image bytes")
        self.assertTrue(photo_path.exists())

        # Verify get_photo_path returns path
        resolved = get_photo_path(test_filename, upload_folder=self.upload_dir)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved, photo_path)

        # 2. Delete existing photo
        deleted = delete_photo(test_filename, upload_folder=self.upload_dir)
        self.assertTrue(deleted)
        self.assertFalse(photo_path.exists())

        # 3. Delete non-existent photo (idempotent, returns False, does not raise exception)
        deleted_again = delete_photo(test_filename, upload_folder=self.upload_dir)
        self.assertFalse(deleted_again)

        # 4. Directory traversal defense: attempt to delete file outside upload directory
        sensitive_file = Path(self.test_dir) / "sensitive_secret.txt"
        sensitive_file.write_bytes(b"confidential system information")
        self.assertTrue(sensitive_file.exists())

        # Traversal attempt with ../sensitive_secret.txt
        res = delete_photo("../sensitive_secret.txt", upload_folder=self.upload_dir)
        self.assertFalse(res)
        self.assertTrue(sensitive_file.exists(), "Traversal attempt must NOT delete outside file.")

        # Traversal attempt with get_photo_path
        self.assertIsNone(get_photo_path("../sensitive_secret.txt", upload_folder=self.upload_dir))

    # ==========================================================================
    # TEST 7B: Photo Storage Adversarial & OS Edge Cases Resilience
    # ==========================================================================
    def test_07b_photo_storage_adversarial_and_os_edge_cases(self):
        """
        Verify delete_photo and get_photo_path handle Windows reserved device names,
        wildcards, invalid characters, null bytes, and traversal attempts safely
        without unhandled OSError or ValueError exceptions.
        """
        adversarial_inputs = [
            # Windows reserved device names (bare and with extensions)
            ("NUL", "Windows NUL device bare"),
            ("nul", "Windows nul device lowercase"),
            ("CON", "Windows CON device bare"),
            ("con", "Windows con device lowercase"),
            ("PRN", "Windows PRN device bare"),
            ("AUX", "Windows AUX device bare"),
            ("COM1", "Windows COM1 serial device"),
            ("LPT1", "Windows LPT1 parallel device"),
            ("CLOCK$", "Windows CLOCK$ device"),
            ("NUL.jpg", "Windows NUL with extension"),
            ("CON.png", "Windows CON with extension"),

            # Invalid filesystem characters and wildcards
            ("*", "Wildcard asterisk bare"),
            ("?", "Wildcard question mark bare"),
            ("test*.jpg", "Wildcard asterisk in filename"),
            ("img?.png", "Wildcard question mark in filename"),
            ("test:stream.jpg", "NTFS alternate data stream colon"),
            ("test<evil>.jpg", "Angle bracket less-than"),
            ("test>evil.jpg", "Angle bracket greater-than"),
            ("test|pipe.jpg", "Pipe character"),

            # Null bytes (triggers ValueError in Win32/C path APIs)
            ("photo.jpg\x00malicious", "Embedded null byte in filename"),
            ("\x00.jpg", "Leading null byte"),
            ("test\x00", "Trailing null byte"),

            # Path traversal variations
            ("../../boot.ini", "Relative unix traversal"),
            (r"..\..\boot.ini", "Relative windows traversal"),
            ("../../../data/voting.db", "Relative traversal to database"),
            ("/etc/passwd", "Unix absolute path"),
            (r"C:\Windows\win.ini", "Windows absolute path"),
            (r"\\attacker\share\payload.jpg", "UNC share path"),

            # Boundary / Malformed / Non-string inputs
            (None, "None object"),
            ("", "Empty string"),
            ("   ", "Whitespace-only string"),
            (12345, "Integer non-string input"),
        ]

        # Decoy file placed outside upload directory to ensure no unintended unlinking
        decoy_file = Path(self.test_dir) / "critical_decoy.txt"
        decoy_file.write_text("CRITICAL DECOY CONTENT", encoding="utf-8")

        for malicious_input, description in adversarial_inputs:
            with self.subTest(operation="delete_photo", input=malicious_input, desc=description):
                try:
                    res = delete_photo(malicious_input, upload_folder=self.upload_dir)
                    self.assertFalse(
                        res,
                        f"delete_photo('{malicious_input}') should return False for {description}, got {res}"
                    )
                except Exception as exc:
                    self.fail(
                        f"delete_photo('{malicious_input}') raised unhandled exception "
                        f"{type(exc).__name__}: {exc} for {description}"
                    )

            with self.subTest(operation="get_photo_path", input=malicious_input, desc=description):
                try:
                    path = get_photo_path(malicious_input, upload_folder=self.upload_dir)
                    self.assertIsNone(
                        path,
                        f"get_photo_path('{malicious_input}') should return None for {description}, got {path}"
                    )
                except Exception as exc:
                    self.fail(
                        f"get_photo_path('{malicious_input}') raised unhandled exception "
                        f"{type(exc).__name__}: {exc} for {description}"
                    )

        # Confirm decoy file was untouched throughout the adversarial battery
        self.assertTrue(decoy_file.is_file(), "Decoy file must remain intact.")
        self.assertEqual(decoy_file.read_text(encoding="utf-8"), "CRITICAL DECOY CONTENT")

    # ==========================================================================
    # TEST 8: End-to-End Foundation Integration Lifecycle
    # ==========================================================================
    def test_08_end_to_end_foundation_lifecycle(self):
        """Simulate candidate registration with photo, vote casting, and admin cascade deletion."""
        conn = self._get_connection()
        try:
            # 1. Save candidate photo
            mock_img = self._create_mock_image(fmt="JPEG", filename="roberto.jpg")
            foto_filename = save_photo(mock_img, upload_folder=self.upload_dir)
            self.assertTrue((self.upload_dir / foto_filename).exists())

            # 2. Add candidate to database
            candidate_id = add_participant(
                "Dr. Roberto Alves",
                "roberto.alves@ints.org.br",
                "Coordenação Médica",
                foto_filename,
                conn=conn
            )
            self.assertGreater(candidate_id, 0)

            # 3. Cast vote from employee
            voter_email = "colaborador.rh@ints.org.br"
            record_votes(voter_email, {candidate_id: 10.0}, conn=conn)

            # 4. Verify leaderboard reflects vote
            board = get_leaderboard(conn=conn)
            self.assertEqual(len(board), 1)
            self.assertEqual(board[0]["nome_completo"], "Dr. Roberto Alves")
            self.assertEqual(board[0]["total_votos"], 1)
            self.assertEqual(board[0]["media_nota"], 10.0)
            self.assertEqual(board[0]["podio"], "ouro")

            # 5. Admin deletes candidate: fetch participant data, delete from DB, delete photo
            cand_data = delete_participant_by_id(candidate_id, conn=conn)
            self.assertIsNotNone(cand_data)
            photo_deleted = delete_photo(cand_data["foto_filename"], upload_folder=self.upload_dir)
            self.assertTrue(photo_deleted)

            # 6. Post-deletion state check
            self.assertEqual(len(list_participants(conn=conn)), 0)
            votes_remaining = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
            self.assertEqual(votes_remaining, 0, "Votes must be automatically deleted by cascade.")
            self.assertFalse((self.upload_dir / foto_filename).exists(), "Photo file must be unlinked from disk.")
        finally:
            conn.close()

    # ==========================================================================
    # TEST 9: Configuration, Directory Auto-Healing, and Security Guards
    # ==========================================================================
    def test_09_config_and_security_guards(self):
        """Verify configuration environments, directory auto-healing, and production security validation."""
        # 1. Development configuration
        dev_cfg = get_config("development")
        self.assertEqual(dev_cfg.PORT, 8080)
        self.assertEqual(dev_cfg.HOST, "0.0.0.0")
        self.assertEqual(dev_cfg.ADMIN_PASSWORD, "admin123")
        self.assertTrue(dev_cfg.DEBUG)

        # 2. Testing configuration
        test_cfg = get_config("testing")
        self.assertTrue(test_cfg.TESTING)
        self.assertEqual(test_cfg.DATABASE_PATH, ":memory:")

        # 3. Production validation fails if ADMIN_PASSWORD is default 'admin123'
        original_env = os.environ.get("APP_ENV")
        original_pass = os.environ.get("ADMIN_PASSWORD")
        try:
            ProductionConfig.ADMIN_PASSWORD = "admin123"
            ProductionConfig.APP_ENV = "production"
            with self.assertRaises(ValueError):
                ProductionConfig.validate()

            # Pass validation with secure password and key
            ProductionConfig.ADMIN_PASSWORD = "SuperSecretSecurePassword2026!"
            ProductionConfig.SECRET_KEY = "a_very_long_and_unguessable_production_key_32_bytes"
            # Should not raise
            ProductionConfig.validate()
        finally:
            if original_env:
                os.environ["APP_ENV"] = original_env
            if original_pass:
                os.environ["ADMIN_PASSWORD"] = original_pass

    # ==========================================================================
    # TEST 9B: Production Configuration Strict Validation Guards
    # ==========================================================================
    def test_09b_production_config_strict_validation(self):
        """
        Verify ProductionConfig strictly enforces non-default secure credentials,
        validates APP_ENV='production', and get_config('production') blocks insecure defaults.
        """
        # Save original attributes to prevent side-effects on subsequent tests
        orig_admin = getattr(ProductionConfig, "ADMIN_PASSWORD", None)
        orig_secret = getattr(ProductionConfig, "SECRET_KEY", None)
        orig_env = getattr(ProductionConfig, "APP_ENV", None)
        orig_os_env = os.environ.get("APP_ENV")
        orig_os_admin = os.environ.get("ADMIN_PASSWORD")
        orig_os_secret = os.environ.get("SECRET_KEY")

        try:
            # 1. ProductionConfig.APP_ENV must be explicitly defined as 'production'
            self.assertEqual(
                ProductionConfig.APP_ENV,
                "production",
                "ProductionConfig must have APP_ENV='production' explicitly set on class."
            )
            self.assertFalse(ProductionConfig.DEBUG, "ProductionConfig.DEBUG must be False.")
            self.assertFalse(ProductionConfig.TESTING, "ProductionConfig.TESTING must be False.")

            # 2. Insecure default password 'admin123' must be rejected
            ProductionConfig.ADMIN_PASSWORD = "admin123"
            ProductionConfig.SECRET_KEY = "very_secure_strong_production_key_32_bytes_long"
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject default password 'admin123'"):
                ProductionConfig.validate()

            # 3. Empty or None password must be rejected
            ProductionConfig.ADMIN_PASSWORD = ""
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject empty password"):
                ProductionConfig.validate()

            ProductionConfig.ADMIN_PASSWORD = None
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject None password"):
                ProductionConfig.validate()

            # 4. Insecure secret key containing 'dev-secret' must be rejected
            ProductionConfig.ADMIN_PASSWORD = "SecureProductionAdminPassword2026!"
            ProductionConfig.SECRET_KEY = "sistema-votacao-ints-dev-secret-key-e7b8c2d1-secure"
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject dev secret key"):
                ProductionConfig.validate()

            # 5. Empty or None secret key must be rejected
            ProductionConfig.SECRET_KEY = ""
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject empty secret key"):
                ProductionConfig.validate()

            ProductionConfig.SECRET_KEY = None
            with self.assertRaises(ValueError, msg="ProductionConfig.validate() must reject None secret key"):
                ProductionConfig.validate()

            # 6. Valid secure settings must pass validation
            ProductionConfig.ADMIN_PASSWORD = "SecureProductionAdminPassword2026!"
            ProductionConfig.SECRET_KEY = "a_genuinely_random_unhackable_secret_token_#2026"
            ProductionConfig.validate()  # Must not raise

            # 7. get_config('production') must trigger validation and fail on default credentials
            ProductionConfig.ADMIN_PASSWORD = "admin123"
            with self.assertRaises(ValueError, msg="get_config('production') must fail when default password is used"):
                get_config("production")

            # 8. get_config('production') must pass when valid credentials are set
            ProductionConfig.ADMIN_PASSWORD = "SecureProductionAdminPassword2026!"
            ProductionConfig.SECRET_KEY = "a_genuinely_random_unhackable_secret_token_#2026"
            prod_cfg = get_config("production")
            self.assertEqual(prod_cfg.APP_ENV, "production")
            self.assertFalse(prod_cfg.DEBUG)
            self.assertFalse(prod_cfg.TESTING)

            # 9. Non-production environments (development, testing) do not raise on default credentials
            dev_cfg = get_config("development")
            self.assertEqual(dev_cfg.ADMIN_PASSWORD, "admin123")
            dev_cfg.validate()  # Must not raise

            test_cfg = get_config("testing")
            test_cfg.validate()  # Must not raise

        finally:
            # Restore all class and environment state
            ProductionConfig.ADMIN_PASSWORD = orig_admin
            ProductionConfig.SECRET_KEY = orig_secret
            ProductionConfig.APP_ENV = orig_env
            if orig_os_env is not None:
                os.environ["APP_ENV"] = orig_os_env
            else:
                os.environ.pop("APP_ENV", None)
            if orig_os_admin is not None:
                os.environ["ADMIN_PASSWORD"] = orig_os_admin
            else:
                os.environ.pop("ADMIN_PASSWORD", None)
            if orig_os_secret is not None:
                os.environ["SECRET_KEY"] = orig_os_secret
            else:
                os.environ.pop("SECRET_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)

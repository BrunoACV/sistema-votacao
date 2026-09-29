"""
app/config.py - Central Configuration Module for INTS Institutional Voting System.
Handles environment variable loading, path resolution, validation, and directory initialization.
"""

import os
from pathlib import Path
from typing import Set
from dotenv import load_dotenv

# Base directory is the project root (sistema-votacao-ints)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env if present (without overriding existing environment)
load_dotenv(BASE_DIR / ".env", override=False)


class Config:
    """Base application configuration with default fallback values."""

    BASE_DIR: Path = BASE_DIR
    APP_ENV: str = os.getenv("APP_ENV", "development").lower()

    # Network Binding
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))

    # Security: Admin Password
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")

    # Security: Session Secret Key
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "sistema-votacao-ints-dev-secret-key-e7b8c2d1-secure"
    )

    # SQLite Database Path
    _raw_db = os.getenv("DATABASE_PATH", "data/voting.db")
    if _raw_db == ":memory:":
        DATABASE_PATH = ":memory:"
    else:
        _db_path = Path(_raw_db)
        DATABASE_PATH = _db_path if _db_path.is_absolute() else BASE_DIR / _db_path

    # Static Photo Upload Directory
    _raw_upload = os.getenv("UPLOAD_FOLDER", "static/uploads")
    _upload_path = Path(_raw_upload)
    UPLOAD_FOLDER: Path = (
        _upload_path if _upload_path.is_absolute() else BASE_DIR / _upload_path
    )

    # Upload Constraints
    MAX_CONTENT_LENGTH: int = int(
        os.getenv("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024))
    )  # 10 MB default
    ALLOWED_IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_MIME_TYPES: Set[str] = {"image/jpeg", "image/png", "image/webp"}

    # Institutional Domain Constraints
    VOTER_EMAIL_DOMAIN: str = "ints.org.br"
    VOTER_EMAIL_REGEX: str = r"^[a-zA-Z0-9_.+-]+@ints\.org\.br$"

    @classmethod
    def validate(cls):
        """Validate production constraints and security settings."""
        if cls.APP_ENV == "production" or cls is ProductionConfig:
            if not cls.ADMIN_PASSWORD or cls.ADMIN_PASSWORD == "admin123":
                raise ValueError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: In production environment, "
                    "ADMIN_PASSWORD must be explicitly set to a secure, non-default value."
                )
            if not cls.SECRET_KEY or "dev-secret" in cls.SECRET_KEY:
                raise ValueError(
                    "CRITICAL SECURITY CONFIGURATION ERROR: In production environment, "
                    "SECRET_KEY must be set to a secure, unguessable random key."
                )

    @classmethod
    def init_directories(cls):
        """Ensure the database and upload directories exist on the filesystem."""
        if cls.DATABASE_PATH != ":memory:":
            Path(cls.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(cls.UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    """Configuration for local development."""
    DEBUG: bool = True
    TESTING: bool = False
    APP_ENV: str = "development"
    HOST: str = os.getenv("DEV_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("DEV_PORT", "8080"))
    ADMIN_PASSWORD: str = os.getenv("DEV_ADMIN_PASSWORD", "admin123")


class TestingConfig(Config):
    """Configuration for automated test execution."""
    DEBUG: bool = True
    TESTING: bool = True
    APP_ENV: str = "testing"
    ADMIN_PASSWORD: str = "test_admin_pass"
    SECRET_KEY: str = "test-secret-key-3b4c5d6e7f"
    DATABASE_PATH: str = ":memory:"


class ProductionConfig(Config):
    """Configuration for production deployment."""
    DEBUG: bool = False
    TESTING: bool = False
    APP_ENV: str = "production"


def get_config(env_name: str = None) -> Config:
    """
    Factory function returning the appropriate configuration instance.
    Validates settings and ensures required directories exist.
    """
    env = (env_name or os.getenv("APP_ENV", "development")).lower()
    if env == "production":
        config_cls = ProductionConfig
    elif env == "testing":
        config_cls = TestingConfig
    else:
        config_cls = DevelopmentConfig

    config_cls.validate()
    config_cls.init_directories()
    return config_cls

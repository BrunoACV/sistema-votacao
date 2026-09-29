"""
app/storage.py - Secure photo storage and validation service for INTS Institutional Voting System.

Handles:
- Directory management for static/uploads/
- Strict multi-layer image validation (Extension, MIME, Size, Pillow verify, Dimensions)
- Safe UUID v4 file persistence
- Path-traversal-proof unlinking (deletion)
"""

import io
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional, Set, Tuple, Union

try:
    from flask import current_app
except ImportError:  # pragma: no cover
    current_app = None

from PIL import Image, UnidentifiedImageError
try:
    from werkzeug.datastructures import FileStorage
except ImportError:  # pragma: no cover
    FileStorage = None


# ==============================================================================
# Configuration Constants
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UPLOAD_FOLDER = PROJECT_ROOT / "static" / "uploads"

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 Megabytes

ALLOWED_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp"}

ALLOWED_MIME_TYPES: Set[str] = {
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/x-png",
    "image/webp",
}

ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP"}

FORMAT_TO_EXTENSIONS = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "WEBP": {".webp"},
}

MAX_IMAGE_DIMENSION = 6000  # Max width / height in pixels

# Windows reserved device names and invalid filename characters for robust sanitization
WINDOWS_RESERVED_NAMES: Set[str] = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

INVALID_FILENAME_CHARS: Set[str] = {"*", "?", ":", "<", ">", "|", "\"", "\x00"}


def _is_invalid_photo_filename(clean_name: str) -> bool:
    """
    Pre-sanitization check detecting null bytes, invalid Windows filename characters,
    or Windows DOS reserved device names.
    """
    if not clean_name or not isinstance(clean_name, str):
        return True
    if any(c in INVALID_FILENAME_CHARS for c in clean_name):
        return True
    # Extract filename component and check stem against reserved DOS device names
    normalized = clean_name.replace("\\", "/").rstrip("/")
    filename_part = normalized.split("/")[-1]
    stem = filename_part.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        return True
    return False



# ==============================================================================
# Exceptions Hierarchy
# ==============================================================================

class StorageError(Exception):
    """Base exception for storage errors."""
    pass


class StorageValidationError(StorageError):
    """Exception raised when an uploaded photo fails validation."""
    pass


class StorageFileTooLargeError(StorageValidationError):
    """Exception raised when the uploaded file exceeds maximum allowed size."""
    pass


class StorageInvalidFormatError(StorageValidationError):
    """Exception raised when file format or MIME type is not allowed."""
    pass


# Alias for compatibility with test suites
InvalidImageError = StorageValidationError


# ==============================================================================
# Directory Management Helpers
# ==============================================================================

def get_upload_dir(upload_folder: Optional[Union[str, Path]] = None) -> Path:
    """
    Resolve the absolute Path for photo uploads.
    Order of precedence:
    1. Explicit parameter 'upload_folder'
    2. current_app.config['UPLOAD_FOLDER']
    3. DEFAULT_UPLOAD_FOLDER (PROJECT_ROOT / 'static' / 'uploads')
    """
    if upload_folder is not None:
        return Path(upload_folder).resolve()

    if current_app:
        configured = current_app.config.get("UPLOAD_FOLDER")
        if configured:
            return Path(configured).resolve()

    return DEFAULT_UPLOAD_FOLDER.resolve()


def ensure_upload_dir(upload_folder: Optional[Union[str, Path]] = None) -> Path:
    """Ensure that the upload directory exists on the filesystem and return its Path."""
    folder = get_upload_dir(upload_folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ==============================================================================
# Stream & Input Extraction Helper
# ==============================================================================

def _extract_stream_and_metadata(file_input: Any) -> Tuple[io.IOBase, str, str]:
    """
    Extracts underlying stream, filename, and content_type from various input types:
    - Werkzeug FileStorage
    - io.BytesIO / io.BufferedIOBase
    - raw bytes
    """
    if file_input is None:
        raise StorageValidationError("Nenhum arquivo enviado. Selecione uma foto.")

    if isinstance(file_input, (bytes, bytearray)):
        return io.BytesIO(file_input), "", ""

    if FileStorage and isinstance(file_input, FileStorage):
        raw_name = file_input.filename or ""
        mime = getattr(file_input, "content_type", "") or ""
        return file_input.stream, raw_name, mime

    # Generic file-like or stream object
    if hasattr(file_input, "stream"):
        stream = file_input.stream
    elif hasattr(file_input, "read") and hasattr(file_input, "seek"):
        stream = file_input
    else:
        raise StorageValidationError("Objeto de arquivo inválido ou não suportado.")

    raw_name = getattr(file_input, "filename", "") or getattr(file_input, "name", "") or ""
    mime = getattr(file_input, "content_type", "") or ""
    return stream, str(raw_name), str(mime)


# ==============================================================================
# Validation Engine
# ==============================================================================

def validate_photo(
    file_input: Any,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES
) -> Tuple[str, str]:
    """
    Validate an uploaded file against all institutional security constraints.

    Checks:
    1. File presence and readable stream
    2. File size inspection (1 byte <= size <= max_size_bytes)
    3. File extension whitelist (if filename is provided)
    4. Client MIME type whitelist (if header is provided)
    5. Pillow image integrity, magic bytes, allowed format, and dimensions
    6. Guarantees stream pointer reset (seek(0))

    Returns:
        Tuple of (verified_pil_format: str, normalized_extension: str)
        e.g. ("JPEG", ".jpg") or ("PNG", ".png")
    """
    stream, raw_filename, content_type = _extract_stream_and_metadata(file_input)

    # 1. Size check via stream seeking
    try:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
    except Exception as e:
        raise StorageValidationError(f"Erro ao ler fluxo do arquivo: {e}") from e

    if size == 0:
        raise StorageValidationError("O arquivo enviado está vazio (0 bytes).")

    if size > max_size_bytes:
        max_mb = max_size_bytes / (1024 * 1024)
        raise StorageFileTooLargeError(
            f"A imagem excede o tamanho máximo permitido de {max_mb:.0f}MB."
        )

    # 2. Extension check (if a filename was specified)
    clean_filename = raw_filename.strip()
    ext = None
    if clean_filename:
        raw_ext = Path(clean_filename).suffix.lower()
        if raw_ext:
            if raw_ext not in ALLOWED_EXTENSIONS:
                allowed_list = ", ".join(sorted(ALLOWED_EXTENSIONS))
                raise StorageInvalidFormatError(
                    f"Extensão de arquivo '{raw_ext}' não suportada. Extensões permitidas: {allowed_list}."
                )
            ext = raw_ext

    # 3. MIME check (if supplied by client)
    clean_mime = content_type.lower().strip()
    if clean_mime and clean_mime not in ALLOWED_MIME_TYPES:
        raise StorageInvalidFormatError(
            f"Tipo MIME '{clean_mime}' não suportado. Envie uma imagem válida (JPEG, PNG ou WEBP)."
        )

    # 4. Pillow deep inspection
    try:
        stream.seek(0)
        with Image.open(stream) as img:
            pil_format = (img.format or "").upper()
            if pil_format not in ALLOWED_PIL_FORMATS:
                raise StorageInvalidFormatError(
                    f"Formato de imagem '{pil_format}' não suportado. Formatos aceitos: JPEG, PNG, WEBP."
                )

            # Check format-extension alignment if an extension was provided
            if ext is not None:
                expected_exts = FORMAT_TO_EXTENSIONS.get(pil_format, set())
                if ext not in expected_exts:
                    raise StorageInvalidFormatError(
                        f"Incompatibilidade entre extensão '{ext}' e formato real '{pil_format}'."
                    )
            else:
                # Infer extension from verified image format
                ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
                ext = ext_map.get(pil_format, ".jpg")

            # Check dimensions
            width, height = img.size
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise StorageValidationError(
                    f"Dimensões da imagem ({width}x{height}) excedem o máximo de "
                    f"{MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION} pixels."
                )

            # Verify integrity
            img.verify()

    except (UnidentifiedImageError, SyntaxError, ValueError, OSError) as e:
        raise StorageValidationError(
            "O arquivo enviado não é uma imagem válida ou está corrompido."
        ) from e
    except Image.DecompressionBombError as e:
        raise StorageValidationError(
            "A imagem excede os limites de segurança contra descompressão."
        ) from e
    finally:
        # Guarantee stream reset
        try:
            stream.seek(0)
        except Exception:
            pass

    # Normalize .jpeg to .jpg
    normalized_ext = ".jpg" if ext in {".jpg", ".jpeg"} else ext
    return pil_format, normalized_ext


# ==============================================================================
# Persistence API
# ==============================================================================

def save_photo(
    file_input: Any,
    upload_folder: Optional[Union[str, Path]] = None,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
) -> str:
    """
    Validate and save a participant photo using a secure UUID v4 filename.

    Args:
        file_input: FileStorage or file-like buffer
        upload_folder: Optional target directory path
        max_size_bytes: Max file size in bytes (defaults to 10MB)

    Returns:
        Generated filename (e.g. 'c9f8a32d1e4b470799f2d1283624bc81.jpg')

    Raises:
        StorageValidationError, StorageFileTooLargeError, StorageInvalidFormatError, StorageError
    """
    _, normalized_ext = validate_photo(file_input, max_size_bytes=max_size_bytes)

    dest_dir = ensure_upload_dir(upload_folder)

    # Generate unique 32-hex UUID v4 filename
    unique_name = f"{uuid.uuid4().hex}{normalized_ext}"
    target_path = dest_dir / unique_name

    # Save to disk
    try:
        stream, _, _ = _extract_stream_and_metadata(file_input)
        stream.seek(0)

        if FileStorage and isinstance(file_input, FileStorage):
            file_input.save(str(target_path))
        else:
            with open(target_path, "wb") as f:
                shutil.copyfileobj(stream, f)

        # Reset stream pointer
        stream.seek(0)
    except Exception as e:
        # Cleanup incomplete file if write failed
        if target_path.exists():
            try:
                target_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise StorageError(f"Falha ao salvar arquivo de foto no disco: {e}") from e

    return unique_name


def delete_photo(
    filename: Optional[str],
    upload_folder: Optional[Union[str, Path]] = None,
) -> bool:
    """
    Safely delete a photo file by filename.

    Security:
    - Path traversal attempts (e.g. '../secret.txt') are detected and rejected.
    - If filename is None, empty, or file doesn't exist: returns False gracefully.
    - Robust against Windows reserved device names, wildcards, and null bytes.

    Returns:
        True if file existed and was removed, False otherwise.
    """
    if not filename or not isinstance(filename, str):
        return False

    clean_name = filename.strip()
    if not clean_name:
        return False

    # Pre-sanitization: reject invalid characters, null bytes, and DOS device names
    if _is_invalid_photo_filename(clean_name):
        return False

    upload_dir = get_upload_dir(upload_folder).resolve()
    try:
        target_path = (upload_dir / clean_name).resolve()
    except (OSError, ValueError):
        return False

    # Path traversal protection: target must be inside upload_dir
    try:
        if not target_path.is_relative_to(upload_dir):
            return False
    except AttributeError:
        # Fallback for older python
        if not str(target_path).startswith(str(upload_dir)):
            return False

    try:
        if target_path.is_file():
            target_path.unlink(missing_ok=True)
            return True
        return False
    except Exception:
        return False


def get_photo_path(
    filename: Optional[str],
    upload_folder: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """
    Resolve and verify existence of a stored photo file.
    Returns Path if valid and exists on disk, None otherwise.
    Robust against Windows reserved device names, wildcards, and null bytes.
    """
    if not filename or not isinstance(filename, str):
        return None

    clean_name = filename.strip()
    if not clean_name:
        return None

    # Pre-sanitization: reject invalid characters, null bytes, and DOS device names
    if _is_invalid_photo_filename(clean_name):
        return None

    upload_dir = get_upload_dir(upload_folder).resolve()
    try:
        target_path = (upload_dir / clean_name).resolve()
    except (OSError, ValueError):
        return None

    try:
        if not target_path.is_relative_to(upload_dir):
            return None
    except AttributeError:
        if not str(target_path).startswith(str(upload_dir)):
            return None

    try:
        if target_path.is_file():
            return target_path
    except (OSError, ValueError):
        return None

    return None

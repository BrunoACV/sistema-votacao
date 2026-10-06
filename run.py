"""
run.py - Main entrypoint for INTS Institutional Voting System.
Starts the Flask WSGI server bound to 0.0.0.0:9090 by default.

Usage:
    python run.py
    python run.py --host 0.0.0.0 --port 9090
    python run.py --env production
"""

import argparse
import os
import socket
import sys
from pathlib import Path

# Ensure root directory is on PYTHONPATH
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from app.config import get_config


def get_local_ip() -> str:
    """Attempt to discover host's local LAN IP for user convenience."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def print_banner(host: str, port: int, env: str, db_path: str, upload_folder: str):
    """Render startup diagnostics and access URLs."""
    local_ip = get_local_ip()
    border = "=" * 70
    print(border)
    print("  INSTITUTO NACIONAL DE TECNOLOGIA E SAÚDE - INTS")
    print("  Sistema de Votação Institucional Web")
    print(border)
    print(f"  Ambiente (APP_ENV)    : {env}")
    print(f"  Host de Escuta        : {host}")
    print(f"  Porta HTTP            : {port}")
    print(f"  Banco de Dados SQLite : {db_path}")
    print(f"  Diretório de Fotos    : {upload_folder}")
    print(border)
    print("  URLs de Acesso:")
    print(f"    - Localhost         : http://localhost:{port}")
    if host in {"0.0.0.0", "::"}:
        print(f"    - Rede Local / IP   : http://{local_ip}:{port}")
    print(f"    - Painel Admin      : http://localhost:{port}/admin")
    print(f"    - Health Check      : http://localhost:{port}/health")
    print(border)
    print("  Pressione CTRL+C para encerrar o servidor.")
    print(border + "\n")


# Standard module-level WSGI instance
app = create_app()


def main():
    """Parse command line arguments and execute server run loop."""
    parser = argparse.ArgumentParser(
        description="Iniciar o servidor do Sistema de Votação Institucional INTS"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("HOST", "0.0.0.0"),
        help="Endereço IP para escuta (padrão: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "9090")),
        help="Porta TCP para escuta (padrão: 9090)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=os.getenv("APP_ENV", "development"),
        choices=["development", "testing", "production"],
        help="Ambiente de execução (development, testing, production)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=None,
        help="Forçar modo debug do Flask",
    )

    args = parser.parse_args()

    # Recreate app if CLI passed an explicit environment
    current_env = os.getenv("APP_ENV", "development").lower()
    if args.env != current_env:
        os.environ["APP_ENV"] = args.env
        server_app = create_app(args.env)
    else:
        server_app = app

    host = args.host
    port = args.port
    debug = args.debug if args.debug is not None else server_app.config.get("DEBUG", False)
    db_path = server_app.config.get("DATABASE_PATH", "data/voting.db")
    upload_folder = server_app.config.get("UPLOAD_FOLDER", "static/uploads")

    print_banner(host, port, args.env, str(db_path), str(upload_folder))

    server_app.run(
        host=host,
        port=port,
        debug=debug,
        threaded=True,
    )


if __name__ == "__main__":
    main()

"""
app/routes/__init__.py - Routes package for INTS Institutional Voting System.
Exposes public and administrative blueprints.
"""

from app.routes.public import public_bp
from app.routes.admin import admin_bp

__all__ = ["public_bp", "admin_bp"]

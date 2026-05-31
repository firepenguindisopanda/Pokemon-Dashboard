"""Flask Blueprints for the Pokemon Dashboard application.

Blueprints:
    auth     — Authentication: login, signup, logout, DB initialization
    pokemon  — Pokemon CRUD: capture, release, rename, search, browsing
    analytics — Analytics dashboard pages and ML API endpoints
"""

from App.blueprints.auth import auth_bp
from App.blueprints.pokemon import pokemon_bp
from App.blueprints.analytics import analytics_bp
from App.blueprints.arena import arena_bp
from App.blueprints.quiz import quiz_bp

__all__ = ["auth_bp", "pokemon_bp", "analytics_bp", "arena_bp", "quiz_bp"]

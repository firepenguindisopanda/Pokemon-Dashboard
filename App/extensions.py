"""Shared Flask extension instances.

These live outside the app factory so blueprints can reference them at import
time (decorators need the object before ``create_app`` runs). They are bound to
the application later via ``init_app``.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Rate limiting is keyed by client IP. Configured and bound in create_app;
# storage is Redis in production so limits survive restarts and span workers.
limiter = Limiter(key_func=get_remote_address)

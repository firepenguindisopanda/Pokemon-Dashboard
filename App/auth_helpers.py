"""Helpers for resolving the authenticated user from a JWT.

RFC 7519 requires the ``sub`` claim to be a string, and PyJWT enforces this
from 2.10 onwards — a token carrying an integer subject fails to decode with
``InvalidSubjectError``. ``User.id`` is an integer column, so the identity is
stored as a string in the token and converted back at every read.
"""

from flask_jwt_extended import get_jwt_identity


def coerce_user_id(identity):
    """Convert a JWT subject claim into a ``User.id`` value.

    Args:
        identity: The raw ``sub`` claim. Normally a string; older tokens
            issued by this app stored an integer or a dict.

    Returns:
        The user id as an int, or None if the identity is unusable.
    """
    if identity is None:
        return None

    if isinstance(identity, dict):
        identity = identity.get("id")

    try:
        return int(identity)
    except (TypeError, ValueError):
        return None


def current_user_id():
    """Return the authenticated user's id as an int, or None.

    Returns:
        The user id from the current request's token.
    """
    return coerce_user_id(get_jwt_identity())

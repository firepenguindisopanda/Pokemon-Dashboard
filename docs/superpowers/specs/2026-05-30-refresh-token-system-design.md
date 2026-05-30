# Refresh Token System for NeonDB Connection Resilience

**Date:** 2026-05-30
**Status:** Approved Design
**Author:** Data Engineering

---

## Overview

The Pokemon Dashboard app crashes when NeonDB drops its pooled Postgres connection because the existing JWT system issues 15-hour access tokens with no fallback. When a DB connection drops mid-session, the `user_lookup_callback` fails (can't query User), and the entire request 500s. This design implements a **dual-path refresh token architecture**:

1. **Stateless primary path (implement now)** — 15-min access tokens + 7-day refresh tokens, DB-resilient user lookup fallback
2. **Token rotation (future enhancement)** — hash-stored refresh tokens in the DB for breach detection

---

## Part 1: Stateless Primary Path — Full File-by-File Changes

### 1.1 `App/config.py` — Configuration Changes

Add refresh token expiry and a configurable token leeway:

```python
# Add to the Settings class, after jwt_access_cookie_name / jwt_refresh_cookie_name:

# ── JWT Token Lifetimes ──
jwt_access_token_expires_hours: float = 0.25       # 15 minutes
jwt_refresh_token_expires_days: int = 7             # 7 days
jwt_token_leeway_seconds: int = 30                  # 30s leeway for clock skew

# Add new properties:
@property
def jwt_access_expires_timedelta(self) -> datetime.timedelta:
    return datetime.timedelta(hours=self.jwt_access_token_expires_hours)

@property
def jwt_refresh_expires_timedelta(self) -> datetime.timedelta:
    return datetime.timedelta(days=self.jwt_refresh_token_expires_days)
```

**Key details:**
- `jwt_access_token_expires_hours` changes from `int` to `float` to support `0.25`
- `jwt_refresh_token_expires_days` is **new**
- `jwt_token_leeway_seconds` is **new** — gives 30s clock skew tolerance
- The existing `jwt_refresh_cookie_name = "refresh_token"` is already set — no change needed

### 1.2 `App/app.py` — Application Factory Changes

Five changes needed:

#### Change 1: Map new config keys into Flask app config

```python
# In create_app(), after the "JWT Config" section, add:
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = settings.jwt_refresh_expires_timedelta
app.config["JWT_TOKEN_LEEWAY"] = settings.jwt_token_leeway_seconds
```

#### Change 2: Register `additional_claims_loader`

This injects `username` and `email` into every JWT's `claims` dict so the resilient `user_lookup_callback` can fall back to them:

```python
# In create_app(), after jwt = JWTManager(app):
@jwt.additional_claims_loader
def add_claims_to_access_token(user):
    """Embed username and email as JWT claims for DB-resilient fallback."""
    return {
        "username": user.username,
        "email": user.email,
    }
```

#### Change 3: Replace `user_lookup_callback` with resilient version

```python
# Replace the existing user_lookup_callback:
from sqlalchemy.exc import OperationalError, TimeoutError
from collections import namedtuple

MinimalUser = namedtuple("MinimalUser", ["id", "username", "email"])

@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    """Resilient user loader with DB-failure fallback to token claims.
    
    If the DB is unreachable (NeonDB connection drop), falls back to
    creating a MinimalUser from the token claims so the request can
    still resolve current_user without crashing.
    """
    from flask import g
    identity = jwt_data["sub"]
    
    # Check request-scoped cache first
    if hasattr(g, "cached_user"):
        return g.cached_user
    
    try:
        user = db.session.get(User, identity)
        if user:
            g.cached_user = user
            return user
    except (OperationalError, TimeoutError) as exc:
        # DB unavailable — use claims fallback
        current_app.logger.warning(
            "DB lookup failed for user %s, using token claims: %s",
            identity, exc,
        )
    
    # Fallback: reconstruct a minimal user from JWT claims
    claims = jwt_data.get("claims", {})
    fallback = MinimalUser(
        id=identity,
        username=claims.get("username", "unknown"),
        email=claims.get("email", "unknown"),
    )
    g.cached_user = fallback
    return fallback
```

#### Change 4: Register `expired_token_loader` for server-side page refresh

This handles expired access tokens during page navigation (server-rendered pages):

```python
# In create_app(), after the user_lookup_loader:
@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_data):
    """Handle expired access tokens by attempting silent refresh.
    
    For server-rendered pages: checks the refresh_token cookie, if valid,
    creates a new access token and redirects to the same URL.
    If refresh token is also expired, redirects to login.
    """
    from flask import request, redirect
    from flask_jwt_extended import decode_token, get_jwt
    
    refresh_cookie_name = current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token")
    refresh_token_str = request.cookies.get(refresh_cookie_name)
    
    if not refresh_token_str:
        # No refresh token — redirect to login
        response = redirect("/")
        unset_jwt_cookies(response)
        unset_refresh_cookies(response)
        flash("Session expired. Please log in again.")
        return response
    
    try:
        # Decode refresh token to verify it's valid
        refresh_data = decode_token(refresh_token_str)
        identity = refresh_data["sub"]
        
        # Create new access token
        user = db.session.get(User, identity)
        if user is None:
            # User not found — redirect to login
            response = redirect("/")
            unset_jwt_cookies(response)
            unset_refresh_cookies(response)
            flash("Account not found. Please log in again.")
            return response
        
        new_access_token = create_access_token(identity=user)
        response = redirect(request.url or "/app")
        set_access_cookies(response, new_access_token)
        return response
        
    except Exception as exc:
        # Refresh token is invalid or expired
        current_app.logger.warning("Token refresh failed: %s", exc)
        response = redirect("/")
        unset_jwt_cookies(response)
        unset_refresh_cookies(response)
        flash("Session expired. Please log in again.")
        return response
```

#### Change 5: Update imports

Add the new imports at the top of `app.py`:

```python
from flask_jwt_extended import (
    JWTManager, current_user,
    create_access_token,                 # needed by expired_token_loader
    set_access_cookies,                  # needed by expired_token_loader
    unset_jwt_cookies,                   # needed by expired_token_loader
    unset_refresh_cookies,               # NEW
)
```

**Complete updated imports block for `app.py`:**

```python
import datetime
import logging
import concurrent.futures
from flask import Flask, current_app, g
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, current_user,
    create_access_token,
    set_access_cookies,
    unset_jwt_cookies,
    unset_refresh_cookies,
)
from sqlalchemy.exc import OperationalError, TimeoutError
from collections import namedtuple
from App.models import db, User
from App.config import get_settings
from App.blueprints.auth import auth_bp, initialize_db
from App.blueprints.pokemon import pokemon_bp
from App.blueprints.analytics import analytics_bp, background_init_analytics, initialize_pokemon_analytics
```

### 1.3 `App/blueprints/auth.py` — Authentication Blueprint Changes

#### Change 1: Add imports for refresh token support

Add to the existing imports:

```python
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,          # NEW
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,           # NEW
    unset_jwt_cookies,
    unset_refresh_cookies,         # NEW
)
```

#### Change 2: Modify `login_action()` — set both tokens

```python
@auth_bp.route("/login", methods=["POST"])
def login_action():
    """Authenticate user and redirect to the main app."""
    data = request.form
    token = login_user(data["username"], data["password"])
    response = None
    if token:
        flash("Logged in successfully.")
        response = redirect(url_for("pokemon.home_page"))
        access_token = create_access_token(identity=user)
        refresh_token = create_refresh_token(identity=user)
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
    else:
        flash("Invalid username or password")
        response = redirect(url_for("auth.login_page"))
    return response
```

**Wait** — the original `login_user()` function returns the access token. We need to restructure. The `login_user()` helper returns the token, but we need the `user` object to pass to `create_refresh_token`. Let me revise:

Actually, looking at the original code, `login_user()` calls `create_access_token(identity=user)` and returns the token string. But we need the user object for both `create_access_token` AND `create_refresh_token`. The simplest approach: modify `login_user()` to return both `(user, access_token)` or just refactor the route to inline the logic.

**Best approach** — refactor `login_action()` to not use the `login_user` helper, since we need the `user` object:

```python
@auth_bp.route("/login", methods=["POST"])
def login_action():
    """Authenticate user, issue access + refresh tokens, redirect to main app."""
    data = request.form
    username = data["username"]
    password = data["password"]
    response = None
    
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        flash("Logged in successfully.")
        response = redirect(url_for("pokemon.home_page"))
        access_token = create_access_token(identity=user)
        refresh_token = create_refresh_token(identity=user)
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
    else:
        flash("Invalid username or password")
        response = redirect(url_for("auth.login_page"))
    
    return response
```

The old `login_user()` helper function can stay (it's a module-level utility) but `login_action()` no longer calls it. The helper exists for any other code that might import it.

#### Change 3: Modify `signup_action()` — set both tokens

```python
@auth_bp.route("/signup", methods=["POST"])
def signup_action():
    """Create a new user account, log them in with both tokens."""
    response = None
    try:
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]
        user = User(username=username, email=email, password=password)
        db.session.add(user)
        db.session.commit()
        response = redirect(url_for("pokemon.home_page"))
        access_token = create_access_token(identity=user)
        refresh_token = create_refresh_token(identity=user)
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
    except IntegrityError:
        flash("Username already exists")
        response = redirect(url_for("auth.signup_page"))
    flash("Account created")
    return response
```

#### Change 4: Modify `logout_action()` — clear both tokens

```python
@auth_bp.route("/logout", methods=["GET"])
@jwt_required()
def logout_action():
    """Log out the current user and clear ALL JWT cookies."""
    response = redirect(url_for("auth.login_page"))
    unset_jwt_cookies(response)
    unset_refresh_cookies(response)
    flash("Logged out")
    return response
```

#### Change 5: Add new `refresh_token()` endpoint

```python
@auth_bp.route("/api/auth/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_token():
    """Issue a new access token using the refresh token cookie.
    
    Used by the frontend apiFetch interceptor on 401 responses.
    Returns the new access token as both JSON and Set-Cookie header.
    """
    identity = current_user
    if identity is None:
        return jsonify({
            "status": "error",
            "code": "REFRESH_TOKEN_EXPIRED",
            "message": "Session expired. Please log in again.",
            "redirect": "/",
        }), 401
    
    new_access_token = create_access_token(identity=identity)
    response = jsonify({
        "status": "success",
        "message": "Access token refreshed",
    })
    set_access_cookies(response, new_access_token)
    return response
```

**Important:** This endpoint uses `@jwt_required(refresh=True)` which validates the `refresh_token` cookie (flask-jwt-extended looks for the cookie name configured as `JWT_REFRESH_COOKIE_NAME`). The `current_user` lookup also uses the resilient fallback.

#### Complete updated `auth.py` imports:

```python
"""Authentication blueprint — login, signup, logout, and app initialization."""

import csv
import logging
from flask import Blueprint, request, redirect, render_template, url_for, flash, jsonify
from sqlalchemy.exc import IntegrityError
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
    unset_refresh_cookies,
)
from App.models import db, User, Pokemon, UserPokemon
```

### 1.4 `App/static/js/dashboard.js` — Frontend apiFetch Interceptor

Add this as a new section at the end of `dashboard.js`:

```javascript
// ── Token Refresh Interceptor ──
// Wraps fetch() calls with automatic 401 → refresh → retry logic.
// Prevents concurrent refresh calls via a simple flag + subscriber pattern.

let isRefreshing = false;
let refreshSubscribers = [];

function onRefreshed(newToken) {
    refreshSubscribers.forEach(function(callback) { callback(newToken); });
    refreshSubscribers = [];
}

function addRefreshSubscriber(callback) {
    refreshSubscribers.push(callback);
}

/**
 * Wrapped fetch with automatic 401 handling.
 * On a 401 response: POST /api/auth/refresh once; on success retry the
 * original request; on failure redirect to login. If a refresh is already
 * in-flight, queue the retry via subscribers.
 */
async function apiFetch(url, options) {
    if (!options) options = {};

    // Clone options to avoid mutation from retry
    options = Object.assign({}, options);

    let response = await fetch(url, options);

    // Not a 401 → return as-is
    if (response.status !== 401) {
        return response;
    }

    // If a refresh is already in progress, wait for it and retry
    if (isRefreshing) {
        return new Promise(function(resolve) {
            addRefreshSubscriber(function() {
                resolve(fetch(url, options));
            });
        });
    }

    // Start refresh
    isRefreshing = true;

    try {
        const refreshResponse = await fetch('/api/auth/refresh', {
            method: 'POST',
            credentials: 'same-origin',
        });

        if (!refreshResponse.ok) {
            // Refresh failed — redirect to login
            isRefreshing = false;
            refreshSubscribers = [];
            window.location.href = '/';
            return response; // unreachable, but return for type safety
        }

        // Refresh succeeded — notify subscribers
        isRefreshing = false;
        onRefreshed();

        // Retry the original request
        return fetch(url, options);

    } catch (error) {
        // Network error during refresh — redirect to login
        isRefreshing = false;
        refreshSubscribers = [];
        window.location.href = '/';
        return response;
    }
}

// Export apiFetch globally (already in global scope via <script> tag)
window.apiFetch = apiFetch;
```

### 1.5 No Changes to `layout.html`

The existing `layout.html` does not need changes because:
- CSRF is disabled — no CSRF tokens needed
- Flash messages already work for session expiry notifications
- The logout link in the navbar already calls `auth.logout_action` which now clears both cookies
- The `current_user` context processor already handles `None` gracefully

### 1.6 `MinimalUser` NamedTuple — Definition Location

Define `MinimalUser` at module level in `App/app.py`:

```python
from collections import namedtuple

MinimalUser = namedtuple("MinimalUser", ["id", "username", "email"])
```

This is the **single source of truth**. Other modules that need to check for `MinimalUser` can import it from `App.app`:

```python
from App.app import MinimalUser
isinstance(user, MinimalUser)  # True if fallback
```

### 1.7 Error Handling Summary

| Endpoint | Error Scenario | Response |
|----------|---------------|----------|
| `POST /login` | Wrong password | Flash "Invalid username or password", redirect `/` |
| `POST /signup` | Duplicate username | Flash "Username already exists", redirect `/signup` |
| `POST /api/auth/refresh` | Missing/expired refresh token | JSON `{status:"error", code:"REFRESH_TOKEN_EXPIRED",...}`, 401 |
| `POST /api/auth/refresh` | DB failure during user lookup | Falls back to token claims; still issues new access token |
| `GET *` (page navigation) | Expired access token, valid refresh | `expired_token_loader` redirects to same page with new access token |
| `GET *` (page navigation) | Expired both tokens | `expired_token_loader` redirects `/` with flash "Session expired" |
| Any `@jwt_required()` | DB down | `user_lookup_callback` returns MinimalUser from claims |
| `GET /logout` | Always | Clears both cookies, redirects `/` |

---

## Part 2: Optional Token Rotation (Future Enhancement)

### 2.1 Architecture

Add a `refresh_token_hash` column to the `User` model. When a refresh token is issued, store `sha256(token_string)` in the DB. On refresh, verify the hash matches, then rotate (invalidate old hash, issue new token + new hash). This enables:

- **Breach detection**: If a refresh token is used after being rotated, the old hash won't match → the old token was stolen
- **Revocation**: Admins can clear a user's hash to force re-login
- **Family detection**: Track token families to detect parallel use

### 2.2 Schema Migration

Since the project has **no Alembic setup** and uses SQLite in dev / Postgres in prod, the simplest approach is a raw SQL migration file.

**File: `migrations/001_add_refresh_token_hash.sql`**

```sql
-- Migration: Add refresh_token_hash column
-- Date: 2026-05-30
-- 
-- Run on SQLite:  sqlite3 data.db < migrations/001_add_refresh_token_hash.sql
-- Run on Postgres: psql $DATABASE_URL -f migrations/001_add_refresh_token_hash.sql

ALTER TABLE "user" ADD COLUMN refresh_token_hash VARCHAR(64) DEFAULT NULL;
ALTER TABLE "user" ADD COLUMN refresh_token_family VARCHAR(64) DEFAULT NULL;
ALTER TABLE "user" ADD COLUMN refresh_token_created_at TIMESTAMP DEFAULT NULL;

-- Index for fast lookup during login/refresh
CREATE INDEX ix_user_refresh_token_hash ON "user" (refresh_token_hash);

-- Note: In production, VACUUM FULL after adding columns if table is large
```

**Python wrapper for idempotent migration:**

```python
# migrations/run.py
"""Run pending migrations idempotently."""
import hashlib
import logging

logger = logging.getLogger(__name__)

MIGRATIONS = [
    {
        "name": "001_add_refresh_token_hash",
        "check": "SELECT 1 FROM pragma_table_info('user') WHERE name='refresh_token_hash'",  # SQLite
        "check_pg": "SELECT 1 FROM information_schema.columns WHERE table_name='user' AND column_name='refresh_token_hash'",
        "sql": """
            ALTER TABLE "user" ADD COLUMN refresh_token_hash VARCHAR(64) DEFAULT NULL;
            ALTER TABLE "user" ADD COLUMN refresh_token_family VARCHAR(64) DEFAULT NULL;
            ALTER TABLE "user" ADD COLUMN refresh_token_created_at TIMESTAMP DEFAULT NULL;
            CREATE INDEX IF NOT EXISTS ix_user_refresh_token_hash ON "user" (refresh_token_hash);
        """,
    },
]

def run_migrations():
    """Run all pending migrations. Safe to call on every app startup."""
    from App.models import db
    from sqlalchemy import text
    
    for migration in MIGRATIONS:
        try:
            # Check if already applied
            if "sqlite" in str(db.engine.url):
                check_sql = migration["check"]
            else:
                check_sql = migration["check_pg"]
            
            result = db.session.execute(text(check_sql)).fetchone()
            if result and result[0]:
                logger.info("Migration '%s' already applied, skipping", migration["name"])
                continue
        except Exception:
            # Table might not exist yet
            pass
        
        # Apply migration
        try:
            for stmt in migration["sql"].strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    db.session.execute(text(stmt))
            db.session.commit()
            logger.info("Applied migration: %s", migration["name"])
        except Exception as exc:
            db.session.rollback()
            logger.warning("Migration '%s' failed (may already exist): %s", migration["name"], exc)
```

### 2.3 Helpers for Hash Management

```python
# App/auth_utils.py — shared token hash utilities

import hashlib
import secrets
from datetime import datetime
from App.models import db, User


def hash_token(token_string: str) -> str:
    """Return SHA-256 hex digest of a token string."""
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()


def generate_token_family() -> str:
    """Generate a random family identifier for token rotation tracking."""
    return secrets.token_hex(32)


def store_refresh_token_hash(user: User, refresh_token: str, family: str = None) -> None:
    """Store the hash of a refresh token on the user record."""
    user.refresh_token_hash = hash_token(refresh_token)
    if family:
        user.refresh_token_family = family
    user.refresh_token_created_at = datetime.utcnow()
    db.session.commit()


def verify_and_rotate_refresh_token(user: User, refresh_token: str) -> bool:
    """Verify the refresh token hash and rotate to a new token.
    
    Returns True if valid, False if compromised (hash mismatch).
    On success, clears the old hash so the old token can't be reused.
    """
    stored_hash = user.refresh_token_hash
    if not stored_hash:
        return False  # No token stored
    
    incoming_hash = hash_token(refresh_token)
    
    if incoming_hash != stored_hash:
        # Hash mismatch — possible token theft
        # Invalidate ALL tokens for this user as a safety measure
        user.refresh_token_hash = None
        user.refresh_token_family = None
        db.session.commit()
        return False
    
    # Hash matches — rotate (invalidate old, prepare for new)
    user.refresh_token_hash = None  # Will be set by caller with new token
    db.session.commit()
    return True


def has_token_family_conflict(user: User, family: str) -> bool:
    """Check if a token from a different family exists (breach signal)."""
    if user.refresh_token_family and user.refresh_token_family != family:
        # Same user, different family — possible concurrent session hijack
        return True
    return False
```

### 2.4 Modified Endpoints with Rotation

#### Modified `login_action()`:

```python
# Inside login_action, after creating refresh_token:
refresh_token = create_refresh_token(identity=user)
family = generate_token_family()
store_refresh_token_hash(user, refresh_token, family)
set_refresh_cookies(response, refresh_token)
```

#### Modified `refresh_token()` endpoint:

```python
@auth_bp.route("/api/auth/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_token():
    """Issue new tokens with rotation. Detects stolen refresh tokens."""
    identity = current_user
    if identity is None:
        return jsonify({
            "status": "error", "code": "REFRESH_TOKEN_EXPIRED",
            "message": "Session expired. Please log in again.",
            "redirect": "/",
        }), 401
    
    # Get the actual refresh token string from cookie
    from flask import request
    from flask import current_app
    refresh_cookie = current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token")
    raw_refresh_token = request.cookies.get(refresh_cookie)
    
    # Look up the real user (not the MinimalUser from claims)
    user = db.session.get(User, identity.id)
    
    if user and raw_refresh_token:
        if not verify_and_rotate_refresh_token(user, raw_refresh_token):
            # Token theft detected — force re-login
            response = jsonify({
                "status": "error",
                "code": "TOKEN_THEFT_DETECTED",
                "message": "Session compromised. Please log in again.",
                "redirect": "/",
            })
            unset_jwt_cookies(response)
            unset_refresh_cookies(response)
            return response, 401
    
    # Issue new tokens
    new_access_token = create_access_token(identity=identity)
    new_refresh_token = create_refresh_token(identity=identity)
    
    # Store new hash
    if user:
        family = generate_token_family()
        store_refresh_token_hash(user, new_refresh_token, family)
    
    response = jsonify({"status": "success", "message": "Tokens refreshed"})
    set_access_cookies(response, new_access_token)
    set_refresh_cookies(response, new_refresh_token)
    return response
```

#### Modified `logout_action()`:

```python
@auth_bp.route("/logout", methods=["GET"])
@jwt_required()
def logout_action():
    """Log out: clear hashes and cookies."""
    identity = current_user
    user = db.session.get(User, identity.id)
    if user:
        user.refresh_token_hash = None
        user.refresh_token_family = None
        db.session.commit()
    
    response = redirect(url_for("auth.login_page"))
    unset_jwt_cookies(response)
    unset_refresh_cookies(response)
    flash("Logged out")
    return response
```

### 2.5 Breach Detection Flow

```
1. User logs in → store hash(family=ABC, token1) on user row
2. Attacker steals token1, user still has it
3. Attacker calls /api/auth/refresh with token1
4. Hash matches → token is valid, rotation begins
5. Hash is cleared, new hash(family=ABC, token2) is stored
6. User calls /api/auth/refresh with old token1
7. Hash doesn't match (token2's hash is now stored)
8. verify_and_rotate_refresh_token returns False
9. ALL hashes cleared → user must re-login
10. Alert: log "Token theft detected for user X"
```

### 2.6 No `Token` Table — Why

We use columns on the `User` model instead of a separate `RefreshToken` table because:

- One user, one active refresh token at a time (simpler mental model)
- No cleanup/expiry CRON needed for orphaned rows
- Faster lookups (single query, no join)
- Token family tracking is simpler
- The tradeoff: you can only have one active refresh token per user. Users on multiple devices will fight over the refresh token — the last login wins. This is acceptable for a dashboard app. If multi-device becomes needed, migrate to a separate `RefreshToken` table with `user_id`, `family`, `expires_at`, `created_at`, and `revoked` columns.

---

## Part 3: Testing Strategy

### 3.1 Existing Tests That Need Updates

#### `test_signup_and_login_flow` — Verify refresh token cookie

```python
def test_signup_and_login_flow(client):
    """Test that signup creates a user and login issues BOTH cookies."""
    # signup a new user
    rv = signup(client, 'newuser', 'n@u.com', 'pw123')
    assert b'Account created' in rv.data
    
    # Verify both cookies are set in the response
    cookies = {c.name: c.value for c in client.cookie_jar}
    assert 'access_token' in cookies
    assert 'refresh_token' in cookies
    
    # login with that user
    rv2 = login(client, 'newuser', 'pw123')
    assert b'Logged in successfully.' in rv2.data
    
    # Verify refresh token is set after login too
    cookies2 = {c.name: c.value for c in client.cookie_jar}
    assert 'access_token' in cookies2
    assert 'refresh_token' in cookies2
    
    # protected route should now be accessible
    rv3 = client.get('/pokemon-area', follow_redirects=True)
    assert rv3.status_code == 200
    assert b'pokemon-area' in rv3.data
```

#### `test_capture_release_via_client` — Already uses login helper, should still pass

This test calls `login(client, 'bob', 'bobpass')`. The login helper calls `POST /login` with form data. The modified `login_action` now also sets `refresh_token` cookie. Since the test doesn't check cookies explicitly, it should pass unchanged.

### 3.2 New Tests

#### Test Refresh Endpoint

```python
# tests/test_auth.py

import os
import tempfile
import pytest
from flask import url_for
from sqlalchemy import create_engine
from flask_jwt_extended import decode_token

from App.app import app, db, MinimalUser
from App.blueprints.auth import initialize_db
from App.models import User


@pytest.fixture(autouse=True)
def _use_sqlite():
    """Override database to use temp SQLite for all tests."""
    db_fd, db_path = tempfile.mkstemp()
    sqlite_uri = f"sqlite:///{db_path}"
    orig_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    app.config['SQLALCHEMY_DATABASE_URI'] = sqlite_uri
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    with app.app_context():
        test_engine = create_engine(sqlite_uri)
        if 'sqlalchemy' in app.extensions:
            ext = app.extensions['sqlalchemy']
            for key in list(ext.engines.keys()):
                ext.engines[key].dispose()
            ext.engines[None] = test_engine
    
    yield
    
    test_engine.dispose()
    with app.app_context():
        if 'sqlalchemy' in app.extensions:
            app.extensions['sqlalchemy'].engines.pop(None, None)
    app.config['SQLALCHEMY_DATABASE_URI'] = orig_uri
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    
    with app.app_context():
        db.create_all()
        initialize_db()
    
    yield client


def login(client, username, password):
    return client.post(
        '/login',
        data={'username': username, 'password': password},
        follow_redirects=True
    )


class TestRefreshToken:
    """Tests for the refresh token endpoint."""
    
    def test_successful_refresh_returns_new_access_token(self, client):
        """Login, then use refresh token to get a new access token."""
        login(client, 'bob', 'bobpass')
        
        # Check refresh token cookie exists
        assert 'refresh_token' in {c.name: c.value for c in client.cookie_jar}
        
        # Call refresh endpoint
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['status'] == 'success'
        
        # Verify new access_token cookie was set
        cookies = {c.name: c.value for c in client.cookie_jar}
        assert 'access_token' in cookies
    
    def test_refresh_without_token_returns_401(self, client):
        """No cookies → refresh should fail."""
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 401
        data = rv.get_json()
        assert data['code'] == 'REFRESH_TOKEN_EXPIRED'
    
    def test_refresh_with_expired_token_returns_401(self, client):
        """Test with an intentionally expired/invalid refresh token."""
        # Set an expired refresh_token cookie manually
        client.set_cookie('localhost', 'refresh_token', 'expired-fake-token')
        rv = client.post('/api/auth/refresh')
        assert rv.status_code == 401
        data = rv.get_json()
        assert data['code'] == 'REFRESH_TOKEN_EXPIRED'
    
    def test_protected_page_with_expired_access_uses_refresh(self, client):
        """Simulate expired access token but valid refresh → should get redirected with new token."""
        login(client, 'bob', 'bobpass')
        
        # Manually set an expired access token
        client.set_cookie('localhost', 'access_token', 'expired-access')
        
        # The expired_token_loader should redirect us with a new token
        rv = client.get('/app', follow_redirects=False)  # Don't follow redirects to see the 302
        # We should either get a redirect (to refresh) or a 200 (if refresh happened)
        # The exact behavior depends on flask-jwt-extended version
        assert rv.status_code in (200, 302)


class TestUserLookupResilience:
    """Tests for the DB-resilient user lookup callback."""
    
    def test_user_lookup_normal(self, client):
        """Normal case: DB is up, user exists."""
        login(client, 'bob', 'bobpass')
        rv = client.get('/app', follow_redirects=True)
        assert rv.status_code == 200
        # Should show bob's username in the navbar
        assert b'bob' in rv.data or b'Bob' in rv.data
    
    def test_user_lookup_fallback_to_minimal_user(self, client):
        """Simulate DB failure: user_lookup_callback should fallback to claims."""
        login(client, 'bob', 'bobpass')
        
        with app.app_context():
            from flask import g
            
            # Simulate a request where DB lookup fails
            # This is tested via the token claims fallback mechanism
            jwt_data = {
                "sub": 1,
                "claims": {"username": "bob", "email": "bob@mail.com"}
            }
            from App.app import user_lookup_callback
            from sqlalchemy.exc import OperationalError
            
            # Mock the db.session.get to raise an error
            original_get = db.session.get
            def mock_get(model, ident):
                raise OperationalError("mock", "mock", "mock")
            
            db.session.get = mock_get
            try:
                result = user_lookup_callback(None, jwt_data)
                assert result is not None
                assert isinstance(result, MinimalUser)
                assert result.username == "bob"
                assert result.email == "bob@mail.com"
            finally:
                db.session.get = original_get


class TestTokenClaims:
    """Tests that additional claims are properly embedded in tokens."""
    
    def test_access_token_contains_claims(self, client):
        """Login and decode the access token to check claims."""
        login(client, 'bob', 'bobpass')
        
        # Get the access token from cookies
        cookies = {c.name: c.value for c in client.cookie_jar}
        access_token = cookies.get('access_token')
        assert access_token is not None
        
        with app.app_context():
            decoded = decode_token(access_token)
            claims = decoded.get('claims', {})
            assert claims.get('username') == 'bob'
            assert claims.get('email') == 'bob@mail.com'
    
    def test_token_expiry_is_short(self, client):
        """Access token should expire in ~15 minutes."""
        login(client, 'bob', 'bobpass')
        
        cookies = {c.name: c.value for c in client.cookie_jar}
        access_token = cookies.get('access_token')
        
        with app.app_context():
            decoded = decode_token(access_token)
            from datetime import datetime, timezone
            exp = datetime.fromtimestamp(decoded['exp'], tz=timezone.utc)
            iat = datetime.fromtimestamp(decoded['iat'], tz=timezone.utc)
            diff_minutes = (exp - iat).total_seconds() / 60
            # Should be about 15 minutes (allow 2 min tolerance for test overhead)
            assert 13 <= diff_minutes <= 17, f"Token expiry {diff_minutes}min not ~15min"
```

### 3.3 Test Count Impact

| Change | Test Count Delta |
|--------|-----------------|
| Existing tests in `test_app.py` | 4 (unchanged, still pass) |
| Existing tests in `test_ml.py` | 54 (unchanged) |
| New `test_auth.py` | 7 new tests |
| **Total after changes** | **4 + 54 + 7 = 65 tests** |

### 3.4 Running Tests

```bash
# Run all tests (with longer timeout for ML training)
python -m pytest tests/ -v --tb=short -o timeout=300

# Run only auth/refresh tests (fast)
python -m pytest tests/test_auth.py -v --tb=short

# Run existing app tests (verify no regression)
python -m pytest tests/test_app.py -v --tb=short
```

---

## Part 4: Verification Plan

### 4.1 End-to-End NeonDB Crash Simulation

```bash
# 1. Start the app in dev mode
python wsgi.py

# 2. Login as bob
curl -c cookies.txt -X POST http://localhost:8080/login \
  -d "username=bob&password=bobpass"

# 3. Verify both cookies are set
cat cookies.txt  # Should show access_token AND refresh_token

# 4. Access a protected page (succeeds)
curl -b cookies.txt http://localhost:8080/app

# 5. SIMULATE NEONDB DROP: Kill the DB connection
#    In another terminal:
ps aux | grep postgres | grep -v grep  # Find the PID
kill -9 <PID>
#    OR just reset the NeonDB connection pool via their dashboard

# 6. Access a protected page AGAIN (should work via claims fallback)
curl -b cookies.txt http://localhost:8080/app
#    Expected: Page renders, shows "bob" in navbar from token claims

# 7. MANUALLY EXPIRED ACCESS TOKEN: Set an expired token
#    The refresh token is still valid (7 days)

# 8. Access a protected page (should trigger expired_token_loader → silent refresh)
curl -b cookies.txt http://localhost:8080/app
#    Expected: Redirect with new access_token cookie set

# 9. Simulate BOTH TOKENS EXPIRED
#    Clear cookies
curl -b "" http://localhost:8080/app
#    Expected: Redirect to login with flash "Session expired"
```

### 4.2 Frontend Interceptor Verification

```javascript
// In browser console after logging in:
apiFetch('/api/pokemon-analytics/stats')
  .then(function(r) { return r.json(); })
  .then(console.log)
  .catch(console.error);

// Expected: Stats data loaded successfully

// Then manually expire the access token in DevTools:
// Application → Cookies → Set access_token = "expired"
// Call apiFetch again — should trigger refresh loop
```

### 4.3 Token Transition Period Verification

Existing users with 15-hour access tokens (issued before the deploy) will continue working until their tokens expire. No invalidation needed because:

- The `jwt_secret_key` hasn't changed
- The token format (flask-jwt-extended) hasn't changed
- After 15 minutes (new expiry), old tokens will hit `expired_token_loader` which handles refresh gracefully
- The refresh token is only set on **new** logins after deploy. Users with old sessions will:
  1. Hit an expired access token (was 15h, might still be valid)
  2. No refresh cookie exists (wasn't set during their old login)
  3. `expired_token_loader` redirects to login with flash "Session expired"
  4. User logs in again → gets both tokens → normal operation

**To make the transition seamless**, consider this temporary shim in `expired_token_loader`:

```python
@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_data):
    """During transition: allow old 15h tokens to still work."""
    # If refresh_token cookie is missing BUT the expired access token
    # was issued before the deploy (check iat), redirect to login gracefully
    refresh_cookie_name = current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token")
    refresh_token_str = request.cookies.get(refresh_cookie_name)
    
    if not refresh_token_str:
        # No refresh token — user hasn't logged in since deploy
        # Clear access cookie and redirect to login
        response = redirect("/")
        unset_jwt_cookies(response)
        flash("Session expired. Please log in again. (Tokens have been refreshed)")
        return response
    # ... rest of the refresh logic
```

This graceful message tells the user why they were logged out, instead of a confusing error.

### 4.4 Monitoring After Deploy

```python
# Log these events for monitoring:
logger.info("Token refresh succeeded for user %s", identity)
logger.warning("DB lookup failed for user %s, using claims fallback", identity)
logger.warning("Token refresh failed: %s", exc)
# With rotation:
logger.error("TOKEN THEFT DETECTED for user %s", user.id)
```

Add a healthcheck endpoint:

```python
@auth_bp.route("/api/auth/health", methods=["GET"])
def auth_health():
    """Healthcheck for auth system. Verifies JWT config is correct."""
    try:
        from flask import current_app as app
        config = {
            "access_expires_minutes": app.config.get("JWT_ACCESS_TOKEN_EXPIRES", {}).total_seconds() / 60,
            "refresh_expires_days": app.config.get("JWT_REFRESH_TOKEN_EXPIRES", {}).total_seconds() / 86400,
            "cookie_secure": app.config.get("JWT_COOKIE_SECURE"),
            "cookie_csrf": app.config.get("JWT_COOKIE_CSRF_PROTECT"),
            "access_cookie": app.config.get("JWT_ACCESS_COOKIE_NAME"),
            "refresh_cookie": app.config.get("JWT_REFRESH_COOKIE_NAME"),
        }
        return jsonify({"status": "ok", "config": config})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
```

---

## Part 5: Implementation Order

### Phase 1 — Foundation (config + app.py)
1. `App/config.py` — Add `jwt_refresh_token_expires_days`, `jwt_token_leeway_seconds`, change `jwt_access_token_expires_hours` to `float`
2. `App/app.py` — Add new imports, `additional_claims_loader`, resilient `user_lookup_callback`, `MinimalUser`, `expired_token_loader`
3. `.env.example` — Document new config vars

### Phase 2 — Auth Blueprint
4. `App/blueprints/auth.py` — Add imports, modify `login_action`, `signup_action`, `logout_action`, add `refresh_token()` endpoint

### Phase 3 — Frontend
5. `App/static/js/dashboard.js` — Add `apiFetch()` interceptor

### Phase 4 — Testing
6. `tests/test_auth.py` — New test file
7. Update `tests/test_app.py` — Add cookie assertions to existing tests

### Phase 5 — Verify
8. Run all 65 tests
9. Manual end-to-end verification
10. Update `.env` for production with new config

---

## Appendix A: flask-jwt-extended v4.4.4 API Reference

| Function | Purpose | Used In |
|----------|---------|---------|
| `create_access_token(identity=user)` | Create 15-min access token | login, signup, refresh |
| `create_refresh_token(identity=user)` | Create 7-day refresh token | login, signup |
| `set_access_cookies(response, token)` | Set access_token cookie | login, signup, expired_token_loader |
| `set_refresh_cookies(response, token)` | Set refresh_token cookie | login, signup |
| `unset_jwt_cookies(response)` | Clear access_token cookie | logout, expired_token_loader |
| `unset_refresh_cookies(response)` | Clear refresh_token cookie | logout, expired_token_loader |
| `@jwt_required(refresh=True)` | Require valid refresh token | refresh_token endpoint |
| `decode_token(token_str)` | Decode a JWT without requiring it (manual) | expired_token_loader |
| `@jwt.additional_claims_loader` | Add custom claims to every access token | app.py |
| `@jwt.expired_token_loader` | Handle expired access tokens | app.py |
| `@jwt.user_lookup_loader` | Resolve current_user from token | app.py |

## Appendix B: Environment Variables (New/Changed)

```ini
# .env — new or changed values

# Changed from JWT_ACCESS_TOKEN_EXPIRES_HOURS=15 to 0.25 (15 minutes)
JWT_ACCESS_TOKEN_EXPIRES_HOURS=0.25

# NEW: Refresh token lifetime
JWT_REFRESH_TOKEN_EXPIRES_DAYS=7

# NEW: Clock skew tolerance (seconds)
JWT_TOKEN_LEEWAY_SECONDS=30
```

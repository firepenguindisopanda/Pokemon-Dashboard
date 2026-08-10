# Pokemon Dashboard App


## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then generate real secrets, see below
```

Generate the two required secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # FLASK_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET_KEY
```

With `DEBUG=false`, the app refuses to start if either secret is a placeholder,
shorter than 32 characters, or empty.

## Database

Schema is managed by Alembic via Flask-Migrate. Seeding is a separate step.

```bash
flask db upgrade    # create/update the schema
flask init          # seed 801 Pokemon and the default users (bob, nick)
```

`flask init` replaces existing seed data and is safe to re-run. It does **not**
create tables — run `flask db upgrade` first.

After changing a model, generate and commit a migration:

```bash
flask db migrate -m "describe the change"
flask db upgrade
```

`tests/test_migrations.py` fails if models and migrations ever disagree.

## Running

```bash
flask run                       # development, port 8080
gunicorn wsgi:app               # production
```

## Testing

```bash
pytest                                    # full suite
pytest --cov=App --cov-report=term-missing
ruff check App/ tests/                    # lint
```

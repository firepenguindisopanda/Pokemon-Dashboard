"""
Model persistence utilities for PokemonAnalytics.
Saves/loads trained models, scalers, and encoders with data hash versioning.
"""

import json
import hashlib
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import joblib
import pandas as pd

logger = logging.getLogger(__name__)

# Overridable so tests can force a cache miss and deployments can point at a
# mounted volume. Defaults to the directory shipped with the package.
MODEL_DIR = Path(os.environ.get('MODEL_CACHE_DIR') or (Path(__file__).parent / 'models_cache'))
MODEL_DIR.mkdir(parents=True, exist_ok=True)
META_FILE = MODEL_DIR / 'manifest.json'


def _timestamp() -> str:
    """Current UTC timestamp for artifact versioning."""
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def resolve_artifact_path(stored: str) -> Path:
    """Turn a manifest path entry into a usable path.

    Manifests now store bare filenames, resolved against MODEL_DIR. Older
    manifests recorded absolute developer-machine paths, which cannot exist on
    another host — fall back to the filename so those still load.

    Args:
        stored: The `path` value from a manifest entry.

    Returns:
        Path to the artifact inside MODEL_DIR.
    """
    candidate = Path(stored)
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        return MODEL_DIR / candidate.name
    return MODEL_DIR / candidate


def _load_manifest() -> dict[str, Any]:
    """Load the model manifest from disk."""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt manifest, starting fresh")
            return {}
    return {}


def _save_manifest(manifest: dict[str, Any]) -> None:
    """Save the model manifest to disk atomically."""
    tmp = META_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(manifest, indent=2, default=str))
    tmp.replace(META_FILE)


def compute_data_hash(df: pd.DataFrame) -> str:
    """Compute a deterministic hash of the training data.

    Args:
        df: The cleaned DataFrame used for training.

    Returns:
        Short hex string (12 chars) identifying the data version.
    """
    return hashlib.md5(
        pd.util.hash_pandas_object(df).values.tobytes()
    ).hexdigest()[:12]


def save_model(
    model: Any,
    name: str,
    data_hash: str,
    metrics: Optional[dict] = None,
) -> str:
    """Save a trained model to disk with version metadata.

    Args:
        model: The sklearn model to persist.
        name: Logical name (e.g. 'legendary_classifier').
        data_hash: Hash of the training data.
        metrics: Optional performance metrics to record.

    Returns:
        Version string for the saved model.
    """
    timestamp = _timestamp()
    version = f"{name}_{timestamp}_{data_hash}"
    path = MODEL_DIR / f"{version}.joblib"

    joblib.dump(model, path)
    logger.info("Saved model %s -> %s", name, path.name)

    manifest = _load_manifest()
    manifest[name] = {
        'version': version,
        'path': path.name,
        'data_hash': data_hash,
        'metrics': metrics or {},
        'saved_at': timestamp,
    }
    _save_manifest(manifest)
    return version


def save_scalers_and_encoders(
    scalers: dict,
    encoders: dict,
    data_hash: str,
) -> None:
    """Save scalers and encoders alongside models."""
    timestamp = _timestamp()
    scalers_path = MODEL_DIR / f"scalers_{data_hash}.joblib"
    encoders_path = MODEL_DIR / f"encoders_{data_hash}.joblib"

    joblib.dump(scalers, scalers_path)
    joblib.dump(encoders, encoders_path)

    manifest = _load_manifest()
    manifest['_scalers'] = {
        'path': scalers_path.name,
        'data_hash': data_hash,
        'saved_at': timestamp,
    }
    manifest['_encoders'] = {
        'path': encoders_path.name,
        'data_hash': data_hash,
        'saved_at': timestamp,
    }
    _save_manifest(manifest)
    logger.info("Saved scalers and encoders for data hash %s", data_hash)


def load_latest_model(name: str, expected_data_hash: str) -> Optional[Any]:
    """Load the latest cached model if the data hash matches.

    Args:
        name: Logical model name.
        expected_data_hash: Current data hash — must match for cache hit.

    Returns:
        The deserialized model, or None if cache is stale or missing.
    """
    manifest = _load_manifest()
    entry = manifest.get(name)
    if entry is None:
        logger.info("No cached model for '%s'", name)
        return None

    if entry.get('data_hash') != expected_data_hash:
        logger.info(
            "Data hash mismatch for '%s' (cached=%s, current=%s), need retrain",
            name, entry.get('data_hash'), expected_data_hash,
        )
        return None

    model_path = resolve_artifact_path(entry['path'])
    if not model_path.exists():
        logger.warning("Cached model file missing: %s", model_path)
        return None

    logger.info("Loading cached model '%s' from %s", name, model_path.name)
    return joblib.load(model_path)


def load_scalers_and_encoders(
    expected_data_hash: str,
) -> tuple[Optional[dict], Optional[dict]]:
    """Load the latest scalers and encoders if data hash matches."""
    manifest = _load_manifest()

    for key in ('_scalers', '_encoders'):
        entry = manifest.get(key)
        if entry is None or entry.get('data_hash') != expected_data_hash:
            return None, None

    scalers = joblib.load(resolve_artifact_path(manifest['_scalers']['path']))
    encoders = joblib.load(resolve_artifact_path(manifest['_encoders']['path']))
    logger.info("Loaded scalers and encoders for data hash %s", expected_data_hash)
    return scalers, encoders


def clear_cache() -> int:
    """Remove all cached models and reset the manifest.

    Returns:
        Number of files removed.
    """
    count = 0
    for f in MODEL_DIR.glob('*'):
        f.unlink()
        count += 1
    _save_manifest({})
    logger.info("Cleared model cache (%d files)", count)
    return count

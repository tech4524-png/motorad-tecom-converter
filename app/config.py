"""
Configuration management for the Motorad Invoice Converter.

All user-editable settings (seller/company info, banking, per-profile folder
paths, country-code overrides, admin credentials) live in a single JSON file
on disk so they can be changed from the web UI without touching code or
reinstalling the service.
"""
import json
import os
import secrets
import threading
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

# Where the live config file lives. In production (packaged EXE) this sits
# next to the executable, inside the install folder, so it survives updates.
CONFIG_DIR = Path(os.environ.get("MOTORAD_CONFIG_DIR", Path(__file__).resolve().parent.parent / "instance"))
CONFIG_PATH = CONFIG_DIR / "config.json"
COUNTRY_CODES_PATH = Path(__file__).resolve().parent / "country_codes.json"

_lock = threading.RLock()  # reentrant: load_config() calls save_config() while holding the lock

DEFAULT_CONFIG = {
    "version": 1,
    "auth": {
        "username": "admin",
        # Default password: Drr2029@@  (changeable from the settings screen)
        "password_hash": generate_password_hash("Drr2029@@"),
    },
    "secret_key": secrets.token_hex(32),
    "seller": {
        "name1": "Motorad Ltd.",
        "street1": "",
        "postal_code": "",
        "city": "",
        "country_code": "IL",
        "country_name": "Israel",
        "tax_registration_number": "",
        "contact_name": "",
        "contact_phone": "",
        "contact_email": "",
        "message_type": "Invoice",
        "message_function": "Original",
        "tax_treatment_code": "NetInvoiceLevel",
        "maker_code": "*",
    },
    "banking": {
        "bank_name": "",
        "bank_account": "",
        "bank_code_number": "",
        "iban": "",
        "swift_bic": "",
    },
    "profiles": [
        {
            "id": "tecom",
            "display_name": "TECOM - חשבונית מס",
            "converter": "tecom",
            "input_dir": "",
            "output_dir": "",
            "archive_dir": "",
            "error_dir": "",
            "enabled": True,
        },
        {
            "id": "ordrsp",
            "display_name": "OrdRsp - תגובת הזמנה",
            "converter": "ordrsp",
            "input_dir": "",
            "output_dir": "",
            "archive_dir": "",
            "error_dir": "",
            "enabled": False,
        },
    ],
    "country_overrides": {},
    # User-created integrations (Phase 2+). Each entry is a full integration
    # config dict as defined in core/integration.py. Builtin profiles (tecom,
    # ordrsp) are NOT stored here — they are generated on-the-fly from the
    # profiles list above.
    "integrations": [],
    # AI configuration (Phase 3) — provider, key, model for field mapping
    "ai": {
        "provider": "none",   # "openai", "anthropic", or "none"
        "api_key": "",
        "model": "",
    },
    "app_version": "1.3.0",
    "language": "he",
}

# Profiles that must exist for every installation, added here over time as
# new formats are supported. load_config() adds any of these that are
# missing from an existing config.json - _deep_merge only merges dict
# values, and "profiles" is a list, so a list entry added to DEFAULT_CONFIG
# above is invisible to an install that already has a config.json on disk
# (the on-disk list wins wholesale). This is what makes a new format's
# profile actually show up after an update, instead of only on fresh
# installs. Existing profiles (paths, enabled state, display_name) are
# never touched - only a profile whose id is missing gets appended.
BUILTIN_PROFILE_IDS = {p["id"] for p in DEFAULT_CONFIG["profiles"]}


def _add_missing_builtin_profiles(cfg):
    existing_ids = {p.get("id") for p in cfg.get("profiles", [])}
    changed = False
    for default_profile in DEFAULT_CONFIG["profiles"]:
        if default_profile["id"] not in existing_ids:
            cfg.setdefault("profiles", []).append(json.loads(json.dumps(default_profile)))
            changed = True
    return changed


def _deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Fill in any missing keys added in newer versions without clobbering
        # what's already on disk.
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, data)
        # "profiles" is a list, not a dict, so _deep_merge above did not
        # touch it - see _add_missing_builtin_profiles' docstring note.
        if _add_missing_builtin_profiles(merged):
            save_config(merged)
        return merged


def save_config(cfg):
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        tmp_path.replace(CONFIG_PATH)


def verify_password(cfg, username, password):
    auth = cfg.get("auth", {})
    if username != auth.get("username"):
        return False
    return check_password_hash(auth.get("password_hash", ""), password)


def set_password(cfg, username, new_password):
    cfg["auth"]["username"] = username
    cfg["auth"]["password_hash"] = generate_password_hash(new_password)


def load_country_codes():
    with open(COUNTRY_CODES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_country_code(country_name, cfg):
    """Resolve a free-text country name to an ISO alpha-2 code.

    Checks user-defined overrides (set via the settings screen) first, then
    the bundled static table. Returns None if unresolved so the caller can
    route the file to the error folder instead of guessing.
    """
    if not country_name:
        return None
    key = country_name.strip().upper()
    overrides = cfg.get("country_overrides", {})
    if key in overrides:
        return overrides[key]
    table = load_country_codes()
    return table.get(key)


# ---------------------------------------------------------------------------
# Integration CRUD (for user-created integrations, Phase 2+)
# ---------------------------------------------------------------------------

def get_integrations(cfg):
    """Return the list of user-created integration configs."""
    return cfg.get("integrations", [])


def get_integration_by_id(cfg, integration_id):
    """Find a single integration by ID. Returns None if not found."""
    for integ in cfg.get("integrations", []):
        if integ.get("id") == integration_id:
            return integ
    return None


def add_integration(cfg, integration_config):
    """Add a new user-created integration to the config.

    Validates the config before adding. Returns (success, error_message).
    """
    from .core.integration import validate_integration_config

    errors = validate_integration_config(integration_config)
    if errors:
        return False, "; ".join(errors)

    # Check for duplicate ID
    existing = get_integration_by_id(cfg, integration_config["id"])
    if existing:
        return False, f"Integration with ID {integration_config['id']!r} already exists"

    cfg.setdefault("integrations", []).append(integration_config)
    save_config(cfg)
    return True, ""


def update_integration(cfg, integration_id, updates):
    """Update an existing integration config (partial update).

    Returns (success, error_message).
    """
    for i, integ in enumerate(cfg.get("integrations", [])):
        if integ.get("id") == integration_id:
            # Merge updates into the existing config
            _deep_merge(integ, updates)
            save_config(cfg)
            return True, ""
    return False, f"Integration {integration_id!r} not found"


def delete_integration(cfg, integration_id):
    """Remove a user-created integration. Returns (success, error_message)."""
    integrations = cfg.get("integrations", [])
    for i, integ in enumerate(integrations):
        if integ.get("id") == integration_id:
            integrations.pop(i)
            save_config(cfg)
            return True, ""
    return False, f"Integration {integration_id!r} not found"


def toggle_integration(cfg, integration_id, enabled):
    """Enable or disable an integration. Returns (success, error_message)."""
    for integ in cfg.get("integrations", []):
        if integ.get("id") == integration_id:
            integ["enabled"] = enabled
            save_config(cfg)
            return True, ""
    return False, f"Integration {integration_id!r} not found"

"""
The admin web UI: login, dashboard, settings (seller/banking/constants,
conversion profiles, country-code overrides), conversion log, and updates.

Runs standalone for development (run_dev.py) or embedded inside the Windows
service (service_windows.py), served over waitress in both cases so the
same code path is exercised in dev and prod.
"""
import functools
import logging
from pathlib import Path

from flask import Flask, request, redirect, url_for, session, render_template, flash

from . import config as config_module
from . import db
from . import updates as updates_module
from . import i18n

logger = logging.getLogger("motorad.webapp")


def create_app(watcher_manager=None, api_poll_manager=None):
    app = Flask(__name__)
    cfg = config_module.load_config()
    app.secret_key = cfg["secret_key"]
    db.init_db()

    # Register webhook blueprint (Phase 2)
    from .webhook_routes import webhook_bp
    app.register_blueprint(webhook_bp)

    def get_config():
        return config_module.load_config()

    @app.context_processor
    def inject_i18n():
        cfg = get_config()
        lang = cfg.get("language", "he")
        return {
            "t": i18n.make_translator(lang),
            "current_lang": lang,
            "is_rtl": i18n.is_rtl(lang),
            "LANGUAGES": i18n.LANGUAGES,
            "APP_NAME": i18n.APP_NAME,
        }

    @app.route("/set-language/<lang_code>")
    def set_language(lang_code):
        if lang_code in i18n.LANGUAGES:
            cfg = get_config()
            cfg["language"] = lang_code
            config_module.save_config(cfg)
        next_url = request.referrer or url_for("login")
        return redirect(next_url)

    def _translator():
        cfg = get_config()
        return i18n.make_translator(cfg.get("language", "he"))

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("logged_in"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            cfg = get_config()
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if config_module.verify_password(cfg, username, password):
                session["logged_in"] = True
                session["username"] = username
                return redirect(request.args.get("next") or url_for("log_view"))
            flash(_translator()("login_error"), "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        return redirect(url_for("log_view"))

    # ------------------------------------------------------------------ #
    # Conversion log
    # ------------------------------------------------------------------ #
    @app.route("/log")
    @login_required
    def log_view():
        status = request.args.get("status") or None
        search = request.args.get("search") or None
        rows = db.fetch_log(limit=300, status=status, search=search)
        return render_template("log.html", rows=rows, status=status, search=search or "")

    # ------------------------------------------------------------------ #
    # Settings: seller / banking / constants / country overrides / password
    # ------------------------------------------------------------------ #
    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings_view():
        cfg = get_config()
        if request.method == "POST":
            form = request.form
            seller = cfg["seller"]
            for field in ("name1", "street1", "postal_code", "city", "country_code",
                          "country_name", "tax_registration_number", "contact_name",
                          "contact_phone", "contact_email", "message_type",
                          "message_function", "tax_treatment_code", "maker_code"):
                if field in form:
                    seller[field] = form.get(field, "").strip()

            banking = cfg["banking"]
            for field in ("bank_name", "bank_account", "bank_code_number", "iban", "swift_bic"):
                if field in form:
                    banking[field] = form.get(field, "").strip()

            config_module.save_config(cfg)
            flash(_translator()("settings_saved"), "success")
            return redirect(url_for("settings_view"))

        return render_template("settings.html", cfg=cfg)

    @app.route("/settings/password", methods=["POST"])
    @login_required
    def change_password():
        cfg = get_config()
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        t = _translator()
        if not config_module.verify_password(cfg, session.get("username", ""), current):
            flash(t("password_current_wrong"), "error")
        elif not new or new != confirm:
            flash(t("password_mismatch"), "error")
        else:
            config_module.set_password(cfg, session.get("username"), new)
            config_module.save_config(cfg)
            flash(t("password_updated"), "success")
        return redirect(url_for("settings_view"))

    # ------------------------------------------------------------------ #
    # Conversion profiles (input/output/archive/error paths per company)
    # ------------------------------------------------------------------ #
    @app.route("/profiles", methods=["GET", "POST"])
    @login_required
    def profiles_view():
        cfg = get_config()
        if request.method == "POST":
            profile_id = request.form.get("profile_id")
            for profile in cfg["profiles"]:
                if profile["id"] == profile_id:
                    profile["input_dir"] = request.form.get("input_dir", "").strip()
                    profile["output_dir"] = request.form.get("output_dir", "").strip()
                    profile["archive_dir"] = request.form.get("archive_dir", "").strip()
                    profile["error_dir"] = request.form.get("error_dir", "").strip()
                    profile["enabled"] = request.form.get("enabled") == "on"
                    break
            config_module.save_config(cfg)
            if watcher_manager:
                watcher_manager.reload()
            flash(_translator()("profiles_saved"), "success")
            return redirect(url_for("profiles_view"))
        return render_template("profiles.html", cfg=cfg)

    # ------------------------------------------------------------------ #
    # Country-code mapping overrides
    # ------------------------------------------------------------------ #
    @app.route("/countries", methods=["GET", "POST"])
    @login_required
    def countries_view():
        cfg = get_config()
        if request.method == "POST":
            t = _translator()
            action = request.form.get("action")
            if action == "add":
                name = request.form.get("name", "").strip().upper()
                code = request.form.get("code", "").strip().upper()
                if name and len(code) == 2:
                    cfg["country_overrides"][name] = code
                    config_module.save_config(cfg)
                    flash(t("countries_added", name=name, code=code), "success")
                else:
                    flash(t("countries_add_error"), "error")
            elif action == "delete":
                name = request.form.get("name", "").strip().upper()
                cfg["country_overrides"].pop(name, None)
                config_module.save_config(cfg)
                flash(t("countries_removed", name=name), "success")
            return redirect(url_for("countries_view"))

        base_table = config_module.load_country_codes()
        return render_template("countries.html", cfg=cfg, base_table=base_table)

    # ------------------------------------------------------------------ #
    # Integrations (Phase 2: webhooks, API polling, API destinations)
    # ------------------------------------------------------------------ #
    @app.route("/integrations", methods=["GET", "POST"])
    @login_required
    def integrations_view():
        cfg = get_config()
        t = _translator()

        if request.method == "POST":
            action = request.form.get("action", "")
            integ_id = request.form.get("integration_id", "")

            # ---- ADD WEBHOOK ----
            if action == "add_webhook":
                from .core.webhook import generate_webhook_path, generate_webhook_secret
                import secrets as _secrets

                name = request.form.get("name", "").strip()
                output_dir = request.form.get("output_dir", "").strip()
                output_format = request.form.get("output_format", "json")
                new_id = f"webhook_{_secrets.token_hex(4)}"

                new_integ = {
                    "id": new_id,
                    "name": name or new_id,
                    "enabled": True,
                    "source": {
                        "type": "webhook",
                        "config": {
                            "webhook_path": generate_webhook_path(),
                            "webhook_secret": generate_webhook_secret(),
                            "content_types": ["application/json"],
                            "require_signature": False,
                        },
                    },
                    "transform": {"type": "passthrough"},
                    "destination": {
                        "type": "file",
                        "config": {
                            "output_dir": output_dir,
                            "format": output_format,
                        },
                    },
                    "error_handling": {"error_dir": ""},
                }
                ok, err = config_module.add_integration(cfg, new_integ)
                if ok:
                    flash(t("integrations_saved"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view", tab="webhooks"))

            # ---- UPDATE WEBHOOK ----
            elif action == "update_webhook":
                updates = {
                    "name": request.form.get("name", "").strip(),
                    "enabled": request.form.get("enabled") == "on",
                    "source": {
                        "config": {
                            "webhook_secret": request.form.get("webhook_secret", ""),
                            "require_signature": request.form.get("require_signature") == "on",
                        },
                    },
                    "destination": {
                        "config": {
                            "output_dir": request.form.get("output_dir", "").strip(),
                            "format": request.form.get("output_format", "json"),
                        },
                    },
                    "error_handling": {
                        "error_dir": request.form.get("error_dir", "").strip(),
                    },
                }
                ok, err = config_module.update_integration(cfg, integ_id, updates)
                if ok:
                    flash(t("integrations_saved"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view", tab="webhooks"))

            # ---- ADD API POLL ----
            elif action == "add_api_poll":
                from .core.api_poll import make_api_poll_source_config
                import secrets as _secrets

                name = request.form.get("name", "").strip()
                url = request.form.get("url", "").strip()
                interval = int(request.form.get("poll_interval_seconds", 300))
                output_dir = request.form.get("output_dir", "").strip()
                output_format = request.form.get("output_format", "json")
                new_id = f"api_poll_{_secrets.token_hex(4)}"

                new_integ = {
                    "id": new_id,
                    "name": name or new_id,
                    "enabled": True,
                    "source": {
                        "type": "api_poll",
                        "config": make_api_poll_source_config(
                            url=url,
                            poll_interval_seconds=interval,
                        ),
                    },
                    "transform": {"type": "passthrough"},
                    "destination": {
                        "type": "file",
                        "config": {
                            "output_dir": output_dir,
                            "format": output_format,
                        },
                    },
                    "error_handling": {"error_dir": ""},
                }
                ok, err = config_module.add_integration(cfg, new_integ)
                if ok:
                    if api_poll_manager:
                        api_poll_manager.reload()
                    flash(t("integrations_saved"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view", tab="api_poll"))

            # ---- UPDATE API POLL ----
            elif action == "update_api_poll":
                updates = {
                    "name": request.form.get("name", "").strip(),
                    "enabled": request.form.get("enabled") == "on",
                    "source": {
                        "config": {
                            "url": request.form.get("url", "").strip(),
                            "method": request.form.get("method", "GET"),
                            "poll_interval_seconds": max(30, int(request.form.get("poll_interval_seconds", 300))),
                            "auth": {"type": request.form.get("auth_type", "none")},
                            "response_format": request.form.get("response_format", "json"),
                            "data_path": request.form.get("data_path", "").strip(),
                        },
                    },
                    "destination": {
                        "config": {
                            "output_dir": request.form.get("output_dir", "").strip(),
                            "format": request.form.get("output_format", "json"),
                        },
                    },
                    "error_handling": {
                        "error_dir": request.form.get("error_dir", "").strip(),
                    },
                }
                ok, err = config_module.update_integration(cfg, integ_id, updates)
                if ok:
                    if api_poll_manager:
                        api_poll_manager.reload()
                    flash(t("integrations_saved"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view", tab="api_poll"))

            # ---- UPDATE API DESTINATION ----
            elif action == "update_api_dest":
                updates = {
                    "name": request.form.get("name", "").strip(),
                    "enabled": request.form.get("enabled") == "on",
                    "destination": {
                        "config": {
                            "url": request.form.get("dest_url", "").strip(),
                            "method": request.form.get("dest_method", "POST"),
                            "content_type": request.form.get("dest_content_type", "application/json"),
                            "auth": {"type": request.form.get("dest_auth_type", "none")},
                            "timeout_seconds": max(5, int(request.form.get("dest_timeout", 30))),
                            "retry_count": max(0, int(request.form.get("dest_retries", 2))),
                            "retry_delay_seconds": max(1, int(request.form.get("dest_retry_delay", 5))),
                        },
                    },
                }
                ok, err = config_module.update_integration(cfg, integ_id, updates)
                if ok:
                    flash(t("integrations_saved"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view", tab="api_dest"))

            # ---- DELETE INTEGRATION ----
            elif action == "delete_integration":
                ok, err = config_module.delete_integration(cfg, integ_id)
                if ok:
                    if api_poll_manager:
                        api_poll_manager.reload()
                    flash(t("integrations_deleted"), "success")
                else:
                    flash(err, "error")
                return redirect(url_for("integrations_view"))

        # ---- GET: render the page ----
        integrations = config_module.get_integrations(cfg)
        active_tab = request.args.get("tab", "webhooks")

        # Split integrations by type
        webhook_integrations = [
            i for i in integrations if i.get("source", {}).get("type") == "webhook"
        ]
        api_poll_integrations = [
            i for i in integrations if i.get("source", {}).get("type") == "api_poll"
        ]
        api_dest_integrations = [
            i for i in integrations if i.get("destination", {}).get("type") == "api"
        ]

        # Attach webhook events for display
        from .core.webhook import get_webhook_events
        for integ in webhook_integrations:
            integ["_events"] = get_webhook_events(integ["id"])

        # Get poll statuses
        poll_statuses = []
        if api_poll_manager:
            poll_statuses = api_poll_manager.get_status()

        return render_template(
            "integrations.html",
            active_tab=active_tab,
            webhook_integrations=webhook_integrations,
            api_poll_integrations=api_poll_integrations,
            api_dest_integrations=api_dest_integrations,
            poll_statuses=poll_statuses,
        )

    # ------------------------------------------------------------------ #
    # Updates
    # ------------------------------------------------------------------ #
    @app.route("/updates", methods=["GET", "POST"])
    @login_required
    def updates_view():
        cfg = get_config()
        if request.method == "POST":
            t = _translator()
            uploaded = request.files.get("package")
            if not uploaded or not uploaded.filename:
                flash(t("updates_missing_file"), "error")
                return redirect(url_for("updates_view"))

            updates_module.UPDATES_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = updates_module.UPDATES_DIR / f"upload_{uploaded.filename}"
            uploaded.save(tmp_path)

            try:
                extract_dir, manifest = updates_module.stage_and_validate(tmp_path, cfg.get("app_version", "1.0.0"))
                updates_module.apply_update(extract_dir, manifest)
                # Scheduled with a short delay (not called directly here) so this
                # request's own HTTP response has time to reach the browser before
                # the service actually stops - see updates.schedule_restart().
                restarted = updates_module.schedule_restart()
                if restarted:
                    flash(t("updates_applied_restarted", version=manifest["version"]), "success")
                else:
                    flash(
                        t("updates_applied_manual_restart", version=manifest["version"], service=updates_module.SERVICE_NAME),
                        "warning",
                    )
            except updates_module.UpdateError as exc:
                flash(t("updates_failed", error=exc), "error")
            finally:
                tmp_path.unlink(missing_ok=True)
            return redirect(url_for("updates_view"))

        return render_template(
            "updates.html",
            current_version=cfg.get("app_version", "1.0.0"),
            backups=updates_module.list_backups(),
        )

    return app

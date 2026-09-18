"""Flask application factory."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def create_app() -> Flask:
    import config
    from src.core.errors import fail

    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=config.DEBUG,
    )

    from . import api, pages, version

    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    # Before anything else can change on disk: what code this process is serving.
    version.mark_started()

    static_dir = BASE_DIR / "static"

    def asset(path: str) -> str:
        """Static URL with the file's modification time, so a browser never keeps
        yesterday's JavaScript after the code changed."""
        from flask import url_for

        try:
            version = int((static_dir / path).stat().st_mtime)
        except OSError:
            version = 0
        return url_for("static", filename=path, v=version)

    app.jinja_env.globals["asset"] = asset

    def _envelope(message: str, code: str, status: int, hint: str = ""):
        body, _ = fail(Exception(message), "router", debug=False)
        body["error"].update(code=code, message=message, hint=hint, detail=None)
        return jsonify(body), status

    @app.errorhandler(404)
    def _not_found(_e):
        return _envelope("Halaman atau endpoint tidak ada.", "NOT_FOUND", 404)

    @app.errorhandler(405)
    def _bad_method(_e):
        # Without this, a wrong verb on an API route returns Werkzeug's HTML
        # page, and the front-end's `response.json()` blows up on it.
        return _envelope(
            "Metode HTTP salah untuk endpoint ini.", "BAD_REQUEST", 405,
            hint="Endpoint kalkulator memakai POST; endpoint data memakai GET.",
        )

    @app.errorhandler(500)
    def _boom(_e):
        return _envelope(
            "Kesalahan internal Bloomberg Hub.", "INTERNAL", 500,
            hint="Ini bug di sini, bukan di sumber datanya. Cek log server.",
        )

    @app.after_request
    def _no_store(response):
        # API answers carry their own cache metadata; never let the browser
        # second-guess it with a stale 200.
        if response.mimetype == "application/json":
            response.headers["Cache-Control"] = "no-store"
        return response

    return app

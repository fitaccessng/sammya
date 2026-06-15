"""
FitAccess Construction ERP
Main entry point for the Flask application.
"""

import os
from app.factory import DEFAULT_DATABASE_URL, create_app


os.environ.setdefault("DATABASE_URL", DEFAULT_DATABASE_URL)


def resolve_runtime_environment():
    """Use development locally and production on hosted deployments unless overridden."""
    explicit_env = os.environ.get("FLASK_ENV")
    if explicit_env:
        return explicit_env
    if os.environ.get("RENDER") or os.environ.get("DATABASE_URL"):
        return "production"
    return "development"


env = resolve_runtime_environment()

app = create_app(env)

if __name__ == "__main__":
    # Only enable debug in development
    debug = env == "development"

    app.run(
        debug=debug,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5050))
    )

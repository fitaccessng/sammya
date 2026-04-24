"""
FitAccess Construction ERP
Main entry point for the Flask application.
"""

import os
from app.factory import create_app

# Default to production (safer for Render)
env = os.environ.get("FLASK_ENV", "production")

app = create_app(env)

if __name__ == "__main__":
    # Only enable debug in development
    debug = env == "development"

    app.run(
        debug=debug,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
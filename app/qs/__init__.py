"""
Quantity Surveyor (QS) Module
"""

from .dashboard import dashboard_bp
from .projects import projects_bp
from .boq import boq_bp
from .reports import reports_bp
from .variations import variations_bp


def register_blueprints(app):
    """Register all QS blueprints with the app."""
    app.register_blueprint(dashboard_bp, url_prefix='/qs')
    app.register_blueprint(projects_bp, url_prefix='/qs')
    app.register_blueprint(boq_bp, url_prefix='/qs')
    app.register_blueprint(reports_bp, url_prefix='/qs')
    app.register_blueprint(variations_bp, url_prefix='/qs')


__all__ = [
    'register_blueprints',
    'dashboard_bp',
    'projects_bp',
    'boq_bp',
    'reports_bp',
    'variations_bp',
]

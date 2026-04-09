"""
Flask application factory and configuration.
Initializes DB, login manager, blueprints, and error handlers.
"""

import os
from flask import Flask, render_template
from flask_login import LoginManager
from flask_mail import Mail
from app.models import db, User


def create_app(config_name='development'):
    """Application factory."""
    app = Flask(__name__)
    
    # Configuration
    if config_name == 'production':
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///fitaccess.db')
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitaccess_dev.db'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        app.config['SECRET_KEY'] = 'dev-secret-key'
    
    # Initialize extensions
    db.init_app(app)
    
    # Initialize Flask-Mail
    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'localhost')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 25))
    app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', False)
    app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', False)
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@fitaccess.com')
    
    mail = Mail(app)
    
    # Initialize login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Register blueprints
    from app.auth import bp as auth_bp
    from app.admin import bp as admin_bp
    from app.procurement import bp as procurement_bp
    from app.cost_control import bp as cost_control_bp
    from app.qc import bp as qc_bp
    from app.finance import bp as finance_bp
    from app.hr import bp as hr_bp
    from app.projects import bp as projects_bp
    from app.qs import register_blueprints as register_qs_blueprints
    from app.api.routes import bp as api_bp
    from app.main import main_bp
    from app.payroll.payroll_routes import payroll_bp
    from app.employee_payroll_routes import employee_payroll_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(procurement_bp)
    app.register_blueprint(cost_control_bp)
    app.register_blueprint(qc_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(projects_bp)
    register_qs_blueprints(app)  # Register modular QS blueprints
    app.register_blueprint(api_bp)
    app.register_blueprint(payroll_bp)  # Payroll management dashboard
    app.register_blueprint(employee_payroll_bp)  # Employee self-service

    # Register all model modules before create_all() so every table is created.
    import app.payroll_models  # noqa: F401
    
    # Error handlers
    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('errors/404.html'), 404
    
    @app.errorhandler(403)
    def forbidden_error(error):
        return render_template('errors/403.html'), 403
    
    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500
    
    # Create tables and return app
    with app.app_context():
        db.create_all()
    
    return app

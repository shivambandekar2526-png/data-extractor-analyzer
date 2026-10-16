"""
Routes and Blueprints Package
=============================
Registers all modular application blueprints on the Flask app instance.
"""

from flask import Flask

def register_blueprints(app: Flask) -> None:
    """Register all modular route blueprints on the Flask application."""
    from routes.views import views_bp
    from routes.extract_api import extract_bp
    from routes.batch_api import batch_bp
    from routes.classify_api import classify_bp
    from routes.tabular_api import tabular_bp
    from routes.history_api import history_bp
    from routes.analysis_api import analysis_bp
    from routes.cleaning_api import cleaning_bp
    from routes.crosscheck_api import crosscheck_bp
    from routes.chat_api import chat_bp
    from routes.settings_api import settings_bp
    from routes.eda_api import eda_bp
    from routes.watcher_api import watcher_bp

    app.register_blueprint(views_bp)
    app.register_blueprint(extract_bp)
    app.register_blueprint(batch_bp)
    app.register_blueprint(classify_bp)
    app.register_blueprint(tabular_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(cleaning_bp)
    app.register_blueprint(crosscheck_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(eda_bp)
    app.register_blueprint(watcher_bp)

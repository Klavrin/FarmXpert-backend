from flask import Flask
from .routes import bp as main_bp

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY="dev")
    app.register_blueprint(main_bp)
    return app
import os
from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import text
from .routes import bp as main_bp
from .match.routes import bp_match
from .apply.routes import bp_apply
from app.subsidies.routes import bp_sub as subsidies_bp
from .db_utilis import engine

from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY="dev")

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    app.register_blueprint(main_bp)  # This includes both / and /api/scraper/*
    app.register_blueprint(bp_match)
    app.register_blueprint(bp_apply)
    app.register_blueprint(subsidies_bp)

    @app.get("/db")
    def db_now():
        try:
            with engine.connect() as conn:
                t = conn.execute(text("select now()")).scalar_one()
                return jsonify({"now": t.isoformat()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app
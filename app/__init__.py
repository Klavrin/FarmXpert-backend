import os
from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import create_engine, text
from .routes import bp as main_bp

from flask_cors import CORS

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY="dev")

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    app.register_blueprint(main_bp)  # This includes both / and /api/scraper/*

    @app.get("/db")
    def db_now():
        try:
            with engine.connect() as conn:
                t = conn.execute(text("select now()")).scalar_one()
                return jsonify({"now": t.isoformat()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app
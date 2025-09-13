import os
from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import create_engine, text
from .routes import bp as main_bp

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])  # sslmode in URL handles TLS

def create_app():
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY="dev")
    app.register_blueprint(main_bp)

    @app.get("/db")
    def db_now():
        try:
            with engine.connect() as conn:
                t = conn.execute(text("select now()")).scalar_one()
                return jsonify({"now": t.isoformat()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        
    return app
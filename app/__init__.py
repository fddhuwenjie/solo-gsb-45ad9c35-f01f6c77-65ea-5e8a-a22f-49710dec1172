import os

from flask import Flask

from .db import init_db
from .routes import bp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DATABASE_PATH", "/data/inspection.db")


def create_app():
    app = Flask(
        __name__,
        static_folder=os.path.join(BASE_DIR, "static"),
        template_folder=os.path.join(BASE_DIR, "templates"),
    )
    app.config["DB_PATH"] = DB_PATH
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(db_dir, exist_ok=True)

    init_db(DB_PATH)

    app.register_blueprint(bp)
    return app

"""电缆隧道接头巡检复核台 —— 应用工厂。"""
import os

from flask import Flask, render_template

from . import db


def create_app():
    app = Flask(
        __name__,
        static_folder="../static",
        template_folder="../templates",
    )
    default_db = os.path.join(os.getcwd(), "data", "cable.db")
    app.config["DATABASE"] = os.environ.get("CABLE_DB", default_db)

    db.init_app(app)

    from . import routes
    app.register_blueprint(routes.bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app

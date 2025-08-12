from flask import Flask
from config.settings import Config
from routes.detect_routes import detect_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Register blueprint
    app.register_blueprint(detect_bp)

    return app

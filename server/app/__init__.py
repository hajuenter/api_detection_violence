from flask import Flask
from config.settings import Config
from routes.detect_routes import detect_bp
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app)
    # Register blueprint
    app.register_blueprint(detect_bp)

    return app

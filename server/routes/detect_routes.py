from flask import Blueprint, request, jsonify, current_app
from app.model_service import detect_fight
import os
import uuid

detect_bp = Blueprint("detect", __name__)

@detect_bp.route("/detect", methods=["POST"])
def detect():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Simpan file sementara
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    # Jalankan deteksi
    result = detect_fight(filepath)

    # Hapus file sementara
    os.remove(filepath)

    return jsonify(result)

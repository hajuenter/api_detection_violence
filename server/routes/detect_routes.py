from flask import Blueprint, request, jsonify, current_app, Response
from app.model_service import detect_fight
import os
import uuid
import cv2
import time

detect_bp = Blueprint("detect", __name__)

# Status terakhir deteksi realtime
last_status = {"label": "normal", "confidence": 0, "timestamp": time.time()}

def update_status(label, confidence):
    """Update status terakhir dengan timestamp sekarang"""
    global last_status
    last_status = {
        "label": label,
        "confidence": confidence,
        "timestamp": time.time()
    }

def generate_frames():
    """Stream kamera dan update status realtime"""
    global last_status

    camera = cv2.VideoCapture(0)

    while True:
        ret, frame = camera.read()
        if not ret:
            break

        # Deteksi fight pada frame
        result = detect_fight(frame)
        label = result.get("label", "normal")
        confidence = result.get("confidence", 0)

        # Update status realtime
        update_status(label, confidence)

        # Gambar bounding box jika ada
        if "bbox" in result:
            for (x1, y1, x2, y2) in result["bbox"]:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

        # Tulis label + confidence di frame
        if label and label.lower() != "unknown":
            cv2.putText(frame, f"{label} ({confidence:.2f})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # Encode frame ke JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 60]
        _, buffer = cv2.imencode('.jpg', frame, encode_param)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    camera.release()

# Endpoint live preview kamera
@detect_bp.route('/snapshot')
def snapshot():
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

# Endpoint deteksi file (tidak mempengaruhi status realtime)
@detect_bp.route("/detect", methods=["POST"])
def detect():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    filename = f"{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    result = detect_fight(filepath)

    try:
        os.remove(filepath)
    except Exception as e:
        current_app.logger.warning(f"Gagal menghapus file sementara: {e}")

    return jsonify(result)

# Endpoint status untuk ESP32
@detect_bp.route('/status')
def status():
    # Reset otomatis ke normal jika tidak ada update > 5 detik
    if time.time() - last_status["timestamp"] > 5:
        return jsonify({"label": "normal", "confidence": 0})
    return jsonify(last_status)

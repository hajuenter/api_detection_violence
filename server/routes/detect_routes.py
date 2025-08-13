from flask import Blueprint, Response, jsonify
from app.model_service import detect_fight
import cv2

detect_bp = Blueprint("detect", __name__)

# Variabel global untuk simpan status
last_status = {"label": "normal", "confidence": 0}

def generate_frames():
    global last_status

    camera = cv2.VideoCapture(0)

    while True:
        ret, frame = camera.read()
        if not ret:
            break

        # --- DETEKSI REALTIME ---
        result = detect_fight(frame)  
        label = result.get("label", "normal")
        confidence = result.get("confidence", 0)

        # Simpan status terbaru
        last_status = {"label": label, "confidence": confidence}

        # Gambar bounding box kalau ada
        if "bbox" in result:
            for (x1, y1, x2, y2) in result["bbox"]:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

        # Tulis label + confidence hanya kalau label valid
        if label and label.lower() != "unknown":
            cv2.putText(frame, f"{label} ({confidence:.2f})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # Encode frame untuk dikirim ke browser
        _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    camera.release()

@detect_bp.route('/snapshot')
def snapshot():
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

# Endpoint untuk ESP membaca status
@detect_bp.route('/status')
def status():
    return jsonify(last_status)

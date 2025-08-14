from flask import Blueprint, request, jsonify, current_app, Response, send_from_directory
from app.model_service import detect_fight
import os
import uuid
import cv2
import time
import logging
import firebase_admin
from firebase_admin import credentials, db

detect_bp = Blueprint("detect", __name__)

DEFAULT_EMAIL = "knuproject86@gmail.com"
DEFAULT_PASSWORD = "knuproject2"

active_tokens = {}

def check_token(req):
    """Cek token dari header Authorization"""
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ")[1]
    return token in active_tokens 

@detect_bp.route("/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    if email == DEFAULT_EMAIL and password == DEFAULT_PASSWORD:
        token = str(uuid.uuid4())
        active_tokens[token] = True  # simpan token aktif
        return jsonify({"success": True, "token": token})

    return jsonify({"success": False, "message": "Invalid credentials"}), 401

@detect_bp.route("/logout", methods=["POST"])
def logout():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "No token provided"}), 400

    token = auth_header.split(" ")[1]
    if token in active_tokens:
        del active_tokens[token]  # hapus token saat logout
        return jsonify({"success": True, "message": "Logged out successfully"})

    return jsonify({"success": False, "message": "Invalid token"}), 401

# Status terakhir deteksi realtime
last_status = {"label": "normal", "confidence": 0, "timestamp": time.time()}

last_fight_alert_time = 0  # Timestamp terakhir kirim alert
FIGHT_ALERT_COOLDOWN = 10  # Deteksi ulang setelah 10 detik

DETECTED_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "detected_images")
os.makedirs(DETECTED_IMAGES_DIR, exist_ok=True)

log_file = os.path.join(os.path.dirname(__file__), "..", "detect_status.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("config/firebase_key.json")  # Pastikan file ini ada
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://knucolab-df3f3-default-rtdb.firebaseio.com/'  # GANTI SESUAI PROJECT
        })
        logging.info("Firebase Admin SDK initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize Firebase: {e}")

def update_status(label, confidence):
    """Update status terakhir dengan timestamp sekarang"""
    global last_status
    last_status = {
        "label": label,
        "confidence": confidence,
        "timestamp": time.time()
    }

def log_fight_event(frame_or_path, confidence):
    global last_fight_alert_time
    current_time = time.time()

    # Cek cooldown: hanya kirim jika sudah lewat dari cooldown
    if current_time - last_fight_alert_time < FIGHT_ALERT_COOLDOWN:
        logging.info(f"Cooldown active. Skipping fight alert (last: {last_fight_alert_time})")
        return  # Skip jika masih dalam masa cooldown

    try:
        # 1. Simpan gambar
        filename = f"fight_{int(current_time)}_{uuid.uuid4().hex[:6]}.jpg"
        filepath = os.path.join(DETECTED_IMAGES_DIR, filename)
        public_url = f"http://localhost:5000/detected_images/{filename}"

        # Baca gambar
        if isinstance(frame_or_path, str):
            img = cv2.imread(frame_or_path)
        else:
            img = frame_or_path

        if img is not None:
            cv2.imwrite(filepath, img)
            logging.info(f"Fight image saved: {filepath}")

            # 2. Kirim ke Firebase
            try:
                alerts_ref = db.reference("/alerts")
                alerts_ref.push({
                    "label": "fight",
                    "confidence": float(confidence),
                    "timestamp": current_time,
                    "image_url": public_url
                })
                logging.info(f"✅ Fight alert sent to Firebase: {public_url}")
                last_fight_alert_time = current_time  # Update waktu terakhir kirim
            except Exception as firebase_error:
                logging.error(f"Firebase push failed: {firebase_error}")
        else:
            logging.error("Failed to read image for saving")
    except Exception as e:
        logging.error(f"Error in log_fight_event: {e}")

@detect_bp.route('/detected_images/<filename>')
def serve_detected_image(filename):
    return send_from_directory(DETECTED_IMAGES_DIR, filename)

def generate_frames():
    global last_status
    camera = cv2.VideoCapture(0)

    while True:
        ret, frame = camera.read()
        if not ret:
            break

        # Deteksi
        result = detect_fight(frame)

        # Gambar bounding box + label
        fight_detected = False
        highest_conf = 0

        for det in result.get("detections", []):
            x1, y1, x2, y2 = map(int, det["bbox"])
            label = det["class_name"].capitalize()
            conf = det.get("confidence", 0) * 100
            color = (0, 0, 255) if label.lower() == "fight" else (0, 255, 0)

            # Gambar kotak
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Teks label
            text = f"{label} ({conf:.1f}%)"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, text, (x1 + 2, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            if label.lower() == "fight":
                fight_detected = True
                if conf / 100 > highest_conf:  # conf dalam desimal untuk simpan
                    highest_conf = conf / 100

        # Update status global (struktur tetap sama)
        if fight_detected:
            update_status("fight", highest_conf)
            # 🔥 Simpan gambar dan kirim ke Firebase
            log_fight_event(frame, highest_conf)
        else:
            # Opsional: reset jika ingin timeout
            if time.time() - last_status["timestamp"] > 5:
                update_status("normal", 0)

        # Encode frame ke JPEG
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

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

    # Ekstrak label dan confidence
    label = result.get("label", "normal")
    confidence = result.get("confidence", 0)

    # Update status global (struktur tetap)
    if label.lower() == "fight":
        update_status("fight", confidence)
        # 🔥 Simpan gambar dan kirim ke Firebase
        log_fight_event(filepath, confidence)
    else:
        update_status("normal", confidence)

    # Hapus file sementara
    try:
        os.remove(filepath)
    except Exception as e:
        current_app.logger.warning(f"Gagal menghapus file sementara: {e}")

    return jsonify(result)

# Endpoint status untuk ESP32
@detect_bp.route('/status')
def status():
    # Reset otomatis ke normal jika tidak ada update > 5 detik
    return jsonify(last_status)

@detect_bp.route('/getrecentalerts', methods=['GET'])
def get_firebase_alerts():
    try:
        alerts_ref = db.reference("/alerts")
        alerts = alerts_ref.get()

        if not alerts:
            return jsonify({"alerts": [], "count": 0}), 200

        alerts_list = []
        for key, value in alerts.items():
            value["id"] = key
            if "timestamp" in value:
                value["timestamp_readable"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(value["timestamp"]))
            alerts_list.append(value)

        # Urutkan berdasarkan timestamp dari terbaru
        alerts_list.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        # Ambil hanya 5 terbaru
        latest_5 = alerts_list[:5]

        return jsonify({
            "alerts": latest_5,
            "count": len(latest_5)
        }), 200

    except Exception as e:
        logging.error(f"Failed to fetch alerts: {e}")
        return jsonify({"error": "Internal server error"}), 500
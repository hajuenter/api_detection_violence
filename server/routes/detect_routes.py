from flask import Blueprint, request, jsonify, current_app, Response, send_from_directory
from app.model_service import detect_fight, detect_car_crash
import os
import uuid
import cv2
import threading
import time
import logging
import firebase_admin
from firebase_admin import credentials, db
from config.settings import UPLOAD_FOLDER_CAR

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

# ✅ PERUBAHAN: Gunakan state-based tracking instead of time-based cooldown
fight_already_logged = False  # Flag untuk track apakah fight sudah disimpan
previous_status = "normal"    # Status sebelumnya untuk detect transisi

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
    """Update status terakhir dengan timestamp sekarang - SELALU UPDATE UNTUK ESP32"""
    global last_status, previous_status, fight_already_logged
    
    # Ambil status sebelumnya
    previous_status = last_status.get("label", "normal")
    
    # ✅ SELALU UPDATE STATUS REALTIME (untuk ESP32 buzzer, dll)
    last_status = {
        "label": label,
        "confidence": confidence,
        "timestamp": time.time()
    }
    
    # ✅ Reset flag ketika transisi dari fight ke normal
    if previous_status.lower() == "fight" and label.lower() == "normal":
        fight_already_logged = False
        logging.info("🔄 Status changed from fight to normal. Ready for next fight detection.")
    
    # ✅ Log setiap perubahan status untuk monitoring
    if previous_status != label:
        logging.info(f"📱 Status updated: {previous_status} → {label} (ESP32 will receive this)")

def log_fight_event(frame_or_path, confidence):
    """✅ PERUBAHAN: Simpan fight hanya jika belum pernah disimpan dalam episode ini"""
    global fight_already_logged
    current_time = time.time()

    # Cek apakah fight sudah pernah disimpan dalam episode ini
    if fight_already_logged:
        logging.info("⏸️  Fight already logged in this episode. Skipping...")
        return

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
            logging.info(f"💾 Fight image saved: {filepath}")

            # 2. Kirim ke Firebase
            try:
                alerts_ref = db.reference("/alerts")
                alerts_ref.push({
                    "label": "fight",
                    "confidence": float(confidence),
                    "timestamp": current_time,
                    "image_url": public_url
                })
                logging.info(f"🔥 Fight alert sent to Firebase: {public_url}")
                
                # ✅ PERUBAHAN: Set flag bahwa fight sudah disimpan
                fight_already_logged = True
                
            except Exception as firebase_error:
                logging.error(f"Firebase push failed: {firebase_error}")
        else:
            logging.error("Failed to read image for saving")
    except Exception as e:
        logging.error(f"Error in log_fight_event: {e}")

@detect_bp.route('/detected_images/<filename>')
def serve_detected_image(filename):
    return send_from_directory(DETECTED_IMAGES_DIR, filename)

# Tambahkan konstanta di atas
FIGHT_CONFIDENCE_THRESHOLD = 0.70  # 70% threshold

def generate_frames():
    """Stream kamera dan update status realtime"""
    global last_status
    camera = cv2.VideoCapture(0)

    while True:
        ret, frame = camera.read()
        if not ret:
            break

        result = detect_fight(frame)
        
        # Inisialisasi default
        label = "normal"
        confidence = 0.0
        
        if result["detections"]:
            # Cari fight dengan confidence >= 70%
            fight_detections = [
                det for det in result["detections"] 
                if det["class_name"].lower() == "fight" and det["confidence"] >= FIGHT_CONFIDENCE_THRESHOLD
            ]
            
            if fight_detections:
                # Ambil fight dengan confidence tertinggi yang >= 70%
                best_fight = max(fight_detections, key=lambda d: d["confidence"])
                label = "fight"
                confidence = best_fight["confidence"]
                # Simpan fight hanya jika belum disimpan dalam episode ini
                log_fight_event(frame, confidence)
                logging.info(f"🔥 Fight detected with confidence: {confidence:.2f}")
            else:
                # Tidak ada fight >= 70%, anggap normal
                label = "normal"
                confidence = 0.0
                logging.debug(f"No fight >= {FIGHT_CONFIDENCE_THRESHOLD} detected")

        # ✅ Update status realtime (ESP32 selalu dapat update terbaru)
        update_status(label, confidence)

        # ✅ Status text di pojok kiri atas (TANPA bounding box)
        status_text = f"Status: {label.upper()}"
        if confidence > 0:
            status_text += f" ({confidence:.2f})"
        
        # Tambahkan indikator jika fight sudah di-log
        if fight_already_logged and label.lower() == "fight":
            status_text += " [LOGGED]"
        
        # Background untuk text status
        (tw, th), _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(frame, (5, 5), (tw + 15, th + 15), (0, 0, 0), -1)
        
        # Warna text: merah untuk fight, hijau untuk normal
        text_color = (0, 0, 255) if label.lower() == "fight" else (0, 255, 0)
        
        cv2.putText(frame, status_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)

        # Encode frame ke JPEG dengan kualitas sedang untuk performa
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

# Endpoint deteksi file
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

    # Update status
    label = result.get("label", "normal")
    confidence = result.get("confidence", 0)
    
    if label.lower() == "fight":
        update_status("fight", confidence)
        # ✅ PERUBAHAN: Simpan fight hanya jika belum disimpan dalam episode ini
        log_fight_event(filepath, confidence)
    else:
        update_status("normal", confidence)

    try:
        os.remove(filepath)
    except Exception as e:
        current_app.logger.warning(f"Gagal menghapus file sementara: {e}")

    return jsonify(result)

# Endpoint status untuk ESP32 - SELALU REALTIME
@detect_bp.route('/status')
def status():
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

status_car = {"value": "normal"}

CAR_CRASH_CONFIDENCE_THRESHOLD = 0.70  
car_crash_already_logged = False
previous_car_status = "normal"
crash_timer = None  # Timer untuk endpoint detect/car_crash

def save_file(file):
    """Helper untuk simpan file sementara"""
    filename = f"{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER_CAR, filename)
    file.save(filepath)
    return filepath

def reset_car_status_after_delay():
    """Reset status car crash ke normal setelah 5 detik (untuk endpoint detect/car_crash)"""
    global crash_timer
    time.sleep(5)
    status_car["value"] = "normal"
    logging.info("⏰ Car status automatically reset to normal after 5 seconds (endpoint detection)")
    crash_timer = None

def update_car_status(label, confidence, from_endpoint=False):
    """Update status car crash dengan state tracking"""
    global previous_car_status, car_crash_already_logged, crash_timer
    
    # Ambil status sebelumnya
    previous_car_status = status_car.get("value", "normal")
    
    # Update status
    status_car["value"] = label
    
    # Jika dari endpoint detect/car_crash dan mendeteksi crash
    if from_endpoint and label == "crash":
        # Cancel timer sebelumnya jika ada
        if crash_timer and crash_timer.is_alive():
            crash_timer = None
        
        # Start timer baru untuk reset ke normal setelah 5 detik
        crash_timer = threading.Thread(target=reset_car_status_after_delay, daemon=True)
        crash_timer.start()
        logging.info("⏰ Started 5-second timer to reset car status to normal")
    
    # Reset flag ketika transisi dari crash ke normal
    if previous_car_status == "crash" and label == "normal":
        car_crash_already_logged = False
        logging.info("🔄 Car status changed from crash to normal. Ready for next crash detection.")
    
    # Log perubahan status
    if previous_car_status != label:
        source = "endpoint" if from_endpoint else "realtime"
        logging.info(f"🚗 Car status updated ({source}): {previous_car_status} → {label} (confidence: {confidence:.2f})")

@detect_bp.route("/detect/car_crash", methods=["POST"])
def detect_car_crash_route():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    filepath = save_file(request.files["file"])

    try:
        result = detect_car_crash(filepath)
        logging.info(f"🔍 ENDPOINT: Detection result: {result}")
        
        # Check if crash detected dengan confidence threshold
        crash_detected = False
        max_confidence = 0.0
        
        if result.get("crash_detected", False):
            if "detections" in result:
                # Cari deteksi crash dengan confidence >= threshold
                crash_detections = [
                    det for det in result["detections"]
                    if "accident" in det.get("class_name", "").lower() or "crash" in det.get("class_name", "").lower()
                ]
                
                if crash_detections:
                    # Ambil yang confidence tertinggi
                    best_crash = max(crash_detections, key=lambda d: d.get("confidence", 0))
                    max_confidence = best_crash.get("confidence", 0)
                    
                    # Untuk endpoint, kita lebih fleksibel dengan threshold atau bisa paksa detect
                    if max_confidence >= CAR_CRASH_CONFIDENCE_THRESHOLD:
                        crash_detected = True
                        logging.info(f"🚨 ENDPOINT: Crash detected with confidence {max_confidence:.3f} >= {CAR_CRASH_CONFIDENCE_THRESHOLD}")
                    else:
                        # Log jika confidence di bawah threshold tapi tetap detect crash untuk endpoint
                        crash_detected = True  # Paksa detect untuk endpoint
                        logging.warning(f"⚠️ ENDPOINT: Crash detected but confidence {max_confidence:.3f} < {CAR_CRASH_CONFIDENCE_THRESHOLD}, but still processing as crash")
        
        # Update status dengan flag from_endpoint=True
        if crash_detected:
            logging.info(f"🎯 ENDPOINT: Updating status to CRASH with confidence {max_confidence:.3f}")
            update_car_status("crash", max_confidence, from_endpoint=True)
        else:
            # Hanya update ke normal jika tidak ada timer yang berjalan
            global crash_timer
            if not (crash_timer and crash_timer.is_alive()):
                logging.info("🎯 ENDPOINT: Updating status to NORMAL (no crash detected)")
                update_car_status("normal", 0.0, from_endpoint=True)
            else:
                logging.info("🎯 ENDPOINT: Timer still running, not updating to normal")
        
        # Log status akhir
        logging.info(f"📊 ENDPOINT: Final car status: {status_car['value']}")
        
    finally:
        os.remove(filepath)
    
    return jsonify(result)

live_running = False
def gen_frames_car():
    """Stream kamera dengan deteksi car crash realtime"""
    global live_running
    cap = cv2.VideoCapture(0)  # 0 = default webcam

    while live_running:   # ✅ hanya jalan kalau live_running = True
        success, frame = cap.read()
        if not success:
            break

        # Simpan frame sementara
        tmp_path = "oke.jpg"
        cv2.imwrite(tmp_path, frame)

        try:
            # Deteksi crash
            result = detect_car_crash(tmp_path)

            crash_detected = False
            max_confidence = 0.0

            if result.get("crash_detected", False):
                if "detections" in result:
                    high_conf_crashes = [
                        det for det in result["detections"]
                        if det.get("confidence", 0) >= CAR_CRASH_CONFIDENCE_THRESHOLD
                        and "crash" in det.get("class_name", "").lower()
                    ]
                    if high_conf_crashes:
                        best_crash = max(high_conf_crashes, key=lambda d: d.get("confidence", 0))
                        crash_detected = True
                        max_confidence = best_crash.get("confidence", 0)

            # ✅ Real-time update status (TIDAK menggunakan timer)
            # Realtime selalu update langsung tanpa delay
            if crash_detected:
                update_car_status("crash", max_confidence, from_endpoint=False)
            else:
                update_car_status("normal", 0.0, from_endpoint=False)

            # Tampilkan status di frame
            status_text = f"Car Status: {status_car['value'].upper()}"
            (tw, th), _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.rectangle(frame, (5, 5), (tw + 15, th + 15), (0, 0, 0), -1)

            text_color = (0, 0, 255) if status_car["value"] == "crash" else (0, 255, 0)
            cv2.putText(frame, status_text, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)

        except Exception as e:
            logging.error(f"Error in car crash detection: {e}")
            update_car_status("normal", 0.0, from_endpoint=False)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass

        # Encode ke JPEG untuk browser
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    cap.release()


@detect_bp.route('/detect/live')
def detect_live():
    """Start live detection"""
    global live_running
    live_running = True
    return Response(gen_frames_car(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@detect_bp.route('/detect/stop', methods=['POST'])
def stop_live():
    """Stop live detection"""
    global live_running
    live_running = False
    return {"message": "Live detection stopped"}

@detect_bp.route("/status/car", methods=["GET"])
def get_status_car():
    """Endpoint untuk ESP32"""
    global crash_timer
    timer_status = "running" if (crash_timer and crash_timer.is_alive()) else "not running"
    logging.info(f"📡 STATUS REQUEST: Car status = {status_car['value']}, Timer = {timer_status}")
    return jsonify({
        "status": status_car["value"]
    })
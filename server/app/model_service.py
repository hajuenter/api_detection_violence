from ultralytics import YOLO
from config.settings import MODEL_PATH, MODEL_PATH1

model = YOLO(str(MODEL_PATH))
car_model = YOLO(str(MODEL_PATH1))
 
CAR_CLASS_MAP = car_model.names 
CLASS_MAP = {0: "normal", 1: "fight"}

print("✅ Model loaded!")

def detect_fight(image_path):
    results = model(image_path)

    detections = []
    fight_detected = False

    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            cls_name = CLASS_MAP.get(cls_id, str(cls_id))
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()

            detections.append({
                "class_id": cls_id,
                "class_name": cls_name,
                "confidence": conf,
                "bbox": xyxy
            })

            if cls_name == "fight":
                fight_detected = True

    return {
        "fight_detected": fight_detected,
        "detections": detections
    }

def detect_car_crash(image_path):
    results = car_model(image_path)

    detections = []

    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            cls_name = CAR_CLASS_MAP.get(cls_id, str(cls_id))
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()

            detections.append({
                "class_id": cls_id,
                "class_name": cls_name,
                "confidence": conf,
                "bbox": xyxy
            })

    # Kalau ada detections → crash_detected = True
    crash_detected = len(detections) > 0

    return {
        "crash_detected": crash_detected,
        "detections": detections
    }

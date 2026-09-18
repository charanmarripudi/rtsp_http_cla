
# from ultralytics import YOLO

# MODEL_PATH = "/Users/algofusion/Downloads/check_best.pt"

# model = YOLO(MODEL_PATH)

# print("\nModel classes:")
# for class_id, class_name in model.names.items():
#     print(f"{class_id}: {class_name}")

# print(f"\nTotal classes: {len(model.names)}")




import time
import cv2
from ultralytics import YOLO

MODEL_PATH = "/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_rpi/models/hansung_ppe_violations.pt"

RTSP_URL = "rtsp://admin:Alg0M0nit%4070%23@192.168.96.41:554/stream1"

# Target FPS configuration
TARGET_FPS = 15
FRAME_TIME = 1.0 / TARGET_FPS

# Load YOLO model
model = YOLO(MODEL_PATH)

# Print model classes so we know their exact names
print("Model classes:")
print(model.names)

# Find the class IDs we want
target_classes = []

for class_id, class_name in model.names.items():
    if class_name in ["NO-Hardhat", "NO-Safety Vest"]:
        target_classes.append(class_id)

print("Detecting class IDs:", target_classes)

# Open RTSP
cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("ERROR: Could not open RTSP stream")
    exit()

print(f"RTSP + YOLO detection started at ~{TARGET_FPS} FPS")
print("Detecting only:")
print("  - NO-Hardhat")
print("  - NO-Safety Vest")
print("Press Q to quit")

prev_time = time.time()

while True:
    ret, frame = cap.read()

    if not ret:
        print("Failed to read frame")
        continue

    now = time.time()
    elapsed = now - prev_time
    if elapsed < FRAME_TIME:
        time.sleep(FRAME_TIME - elapsed)
    prev_time = time.time()

    # Run YOLO on CURRENT frame with 640px resolution for real-time tracking
    results = model.predict(
        source=frame,
        conf=0.20,
        iou=0.45,
        imgsz=640,
        classes=target_classes,
        verbose=False
    )

    # Draw detections
    annotated_frame = results[0].plot()

    cv2.imshow("NO-Hardhat / NO-Safety Vest Detection", annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
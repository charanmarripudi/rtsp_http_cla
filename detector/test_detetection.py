
# from ultralytics import YOLO

# MODEL_PATH = "/Users/algofusion/Downloads/check_best.pt"

# model = YOLO(MODEL_PATH)

# print("\nModel classes:")
# for class_id, class_name in model.names.items():
#     print(f"{class_id}: {class_name}")

# print(f"\nTotal classes: {len(model.names)}")




import os
# Force TCP transport and optimal buffers to eliminate H.264 packet drop and smearing
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|max_delay;500000|timeout;5000000"

import time
import threading
import cv2
from ultralytics import YOLO

MODEL_PATH = "/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_rpi/models/hansung_ppe_violations.pt"

RTSP_URL = "rtsp://192.168.96.72:8554/ppe_stream3"

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
    if class_name in ["NO-Hardhat", "NO-Safety Vest", "Hardhat", "Safety Vest"]:
        target_classes.append(class_id)

print("Detecting class IDs:", target_classes)

# Dedicated Frame Grabber to eliminate stream buffer latency on moving objects
class RTSPCaptureThread:
    def __init__(self, url):
        self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.latest_frame = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._grab_loop, daemon=True)
        self.thread.start()

    def _grab_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.latest_frame = frame
                    self.ret = True
            else:
                time.sleep(0.01)

    def read(self):
        with self.lock:
            if self.latest_frame is not None:
                return self.ret, self.latest_frame.copy()
            return False, None

    def release(self):
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()

cap = RTSPCaptureThread(RTSP_URL)
time.sleep(1.0) # Wait for initial frames

print(f"RTSP + YOLO detection started (TCP Transport, Zero-Lag Grabber)")
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
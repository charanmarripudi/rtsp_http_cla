from ultralytics import YOLO

model = YOLO("/Users/charanmarripudi/Documents/rtsp_rpi/models/hansung_ppe_violations.pt")
print(model.names)
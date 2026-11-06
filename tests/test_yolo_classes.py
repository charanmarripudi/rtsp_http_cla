from ultralytics import YOLO

model = YOLO("/Users/charanmarripudi/Documents/rtsp_rpi/models/firehose.pt")
print(model.names)
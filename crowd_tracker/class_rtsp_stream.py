import cv2
import threading
import time


class RTSPStream:
    """Threaded video stream reader supporting both RTSP streams and local video files.
    
    When reading from a local video file, it paces frames according to video FPS
    and automatically loops back to the start.
    """

    def __init__(self, src):
        self.src = src
        self.cap = cv2.VideoCapture(src)
        self.frame = None
        self.stopped = False
        
        self.lock = threading.Lock()
        
        # Calculate frame pacing
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.delay = 1.0 / fps if fps and fps > 0 else 0.03
        self.is_file = not (isinstance(src, str) and src.startswith("rtsp://"))
        
        self.t = threading.Thread(target=self.update, args=(), daemon=True)
        self.t.start()

    def update(self):
        while not self.stopped:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.frame = frame
                if self.is_file:
                    time.sleep(self.delay)
            else:
                if self.is_file:
                    # Loop video continuously
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    time.sleep(0.02)
                else:
                    time.sleep(0.1)

    def read(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def release(self):
        self.stopped = True
        if self.cap.isOpened():
            self.cap.release()

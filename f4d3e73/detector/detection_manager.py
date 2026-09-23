import subprocess
import os

class DetectionManager:

    def __init__(self, base_dir):
        self.base_dir = base_dir

        # camera_id -> process
        self.processes = {}

        # camera_id -> model
        self.active_models = {}

    #####################################################
    # START DETECTION
    #####################################################
    def start(self, camera_id, rtsp_url, model_name):

        self.stop(camera_id)

        cmd = [
            "python3",
            os.path.join(self.base_dir, "detector", "start_detection.py"),
            str(camera_id),
            rtsp_url,
            model_name
        ]

        print("Starting detection:", cmd)

        p = subprocess.Popen(cmd)

        self.processes[camera_id] = p
        self.active_models[camera_id] = model_name

        return {"status": "started", "camera": camera_id}

    #####################################################
    # STOP DETECTION
    #####################################################
    def stop(self, camera_id):

        if camera_id in self.processes:

            try:
                self.processes[camera_id].terminate()
            except:
                pass

            del self.processes[camera_id]

        if camera_id in self.active_models:
            del self.active_models[camera_id]

        return {"status": "stopped", "camera": camera_id}

    #####################################################
    # STATUS
    #####################################################
    def status(self):
        return {
            "active": list(self.processes.keys()),
            "models": self.active_models
        }
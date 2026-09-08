#!/usr/bin/env python3
"""
Step-by-Step Zoom & PTZ Terminal Tester

Usage:
  python test_zoom.py in        # Single step Zoom In (+)
  python test_zoom.py out       # Single step Zoom Out (-)
  python test_zoom.py up        # Single step Move Up
  python test_zoom.py down      # Single step Move Down
  python test_zoom.py left      # Single step Move Left
  python test_zoom.py right     # Single step Move Right
  python test_zoom.py           # Interactive key press mode (+, -, w, a, s, d)
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utilities.onvif_controller import OnvifController

IP = "192.168.96.30"
PORT = 80
USER = "admin"
PASS = ""


def single_action(action: str):
    controller = OnvifController(ip=IP, port=PORT, username=USER, password=PASS)
    act_lower = action.lower()
    print(f"▶ Executing: '{act_lower}' on {IP}:{PORT}...")

    if act_lower in ["in", "+", "zoomin", "zoom_in"]:
        controller.zoom(0.5)
        print("✅ Zoom In (+) step completed!")
    elif act_lower in ["out", "-", "zoomout", "zoom_out"]:
        controller.zoom(-0.5)
        print("✅ Zoom Out (-) step completed!")
    elif act_lower in ["up", "top", "w"]:
        controller.pan_tilt(0.0, 0.5)
        print("✅ Move Up step completed!")
    elif act_lower in ["down", "bottom", "s"]:
        controller.pan_tilt(0.0, -0.5)
        print("✅ Move Down step completed!")
    elif act_lower in ["left", "a"]:
        controller.pan_tilt(-0.5, 0.0)
        print("✅ Move Left step completed!")
    elif act_lower in ["right", "d"]:
        controller.pan_tilt(0.5, 0.0)
        print("✅ Move Right step completed!")
    else:
        print(f"Unknown action: {action}")


def interactive_mode():
    print("=" * 60)
    print(f" Interactive Camera Control Terminal for http://{IP}:{PORT}")
    print(" Controls:")
    print("   '+' or 'i' : Zoom In (+)")
    print("   '-' or 'o' : Zoom Out (-)")
    print("   'w' or 'u' : Move Up")
    print("   's' or 'd' : Move Down")
    print("   'a' or 'l' : Move Left")
    print("   'd' or 'r' : Move Right")
    print("   'q'        : Quit")
    print("=" * 60)

    controller = OnvifController(ip=IP, port=PORT, username=USER, password=PASS)

    while True:
        try:
            cmd = input("\nEnter key (+ / - / w / a / s / d / q) > ").strip().lower()
            if cmd == 'q':
                print("Exiting interactive mode.")
                break
            elif cmd in ['+', 'i', 'in']:
                print("▶ Zooming In (+)...")
                controller.zoom(0.5)
                print("  Done!")
            elif cmd in ['-', 'o', 'out']:
                print("▶ Zooming Out (-)...")
                controller.zoom(-0.5)
                print("  Done!")
            elif cmd in ['w', 'u', 'up']:
                print("▶ Moving Up...")
                controller.pan_tilt(0.0, 0.5)
                print("  Done!")
            elif cmd in ['s', 'down']:
                print("▶ Moving Down...")
                controller.pan_tilt(0.0, -0.5)
                print("  Done!")
            elif cmd in ['a', 'left']:
                print("▶ Moving Left...")
                controller.pan_tilt(-0.5, 0.0)
                print("  Done!")
            elif cmd in ['d', 'right']:
                print("▶ Moving Right...")
                controller.pan_tilt(0.5, 0.0)
                print("  Done!")
            else:
                print("Invalid key. Use + for Zoom In, - for Zoom Out, w/a/s/d for Pan/Tilt, q to quit.")
        except KeyboardInterrupt:
            print("\nExiting.")
            break
        except Exception as e:
            print(f"Error executing command: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        single_action(sys.argv[1])
    else:
        interactive_mode()

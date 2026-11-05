import os
import sys
import time

# Ensure backend modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'detector')))

from detector_worker import is_opposite_class, is_negative_violation_class

def test_absence_rules():
    print("Testing absence detection logic...")

    # 1. Negative class classification
    assert is_negative_violation_class("no-wheel-choke") == True
    assert is_negative_violation_class("no-Fire_Extinguisher") == True
    assert is_negative_violation_class("no-Sand-Bucket") == True
    assert is_negative_violation_class("no-Spark_Arrestor") == True
    assert is_negative_violation_class("no-fire_hose") == True
    assert is_negative_violation_class("no-tape") == True

    # 2. Positive classes must NOT be negative violation classes
    assert is_negative_violation_class("wheel-choke") == False
    assert is_negative_violation_class("Fire_Extinguisher") == False
    assert is_negative_violation_class("Sand-Bucket") == False
    assert is_negative_violation_class("Spark_Arrestor") == False
    assert is_negative_violation_class("fire_hose") == False

    # 3. Opposite class matching (model detects positive, rule monitors negative)
    assert is_opposite_class("wheel-choke", "no-wheel-choke") == True
    assert is_opposite_class("Fire_Extinguisher", "no-Fire_Extinguisher") == True
    assert is_opposite_class("Sand-Bucket", "no-Sand-Bucket") == True
    assert is_opposite_class("Spark_Arrestor", "no-Spark_Arrestor") == True

    print("ALL ABSENCE TESTS PASSED!")

if __name__ == '__main__':
    test_absence_rules()


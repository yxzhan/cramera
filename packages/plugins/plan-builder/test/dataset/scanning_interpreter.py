#!/usr/bin/env python3
"""
Stands in for the interpreter running cramera-bridge's scanner: called as
``<interpreter> -m <module> <order file> <scan file>``, once its input closes, it writes
a scan in which the camera saw the order's simulated objects.
"""

import json
import sys

_, _, order_file, scan_file = sys.argv[1:]
sys.stdin.read()
with open(order_file, encoding="utf-8") as order:
    simulated = json.load(order)["simulatedObjects"]
with open(scan_file, "w", encoding="utf-8") as scan:
    json.dump({"objects": simulated, "grounding": "perceived", "unrecognized": 0}, scan)

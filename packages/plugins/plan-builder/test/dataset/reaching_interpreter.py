#!/usr/bin/env python3
"""
Stands in for the interpreter running cramera-bridge's reach command: called as
``<interpreter> -m <module> <order file> <answer file>``, once its input closes, it
writes that the robot's one arm reaches 1 m around a shoulder above the origin, and
counts its calls in a file beside the answer's directory named by the environment.
"""

import json
import os
import sys

_, _, order_file, answer_file = sys.argv[1:]
sys.stdin.read()
with open(order_file, encoding="utf-8") as order:
    robot = json.load(order)["robot"]
calls = os.environ.get("REACH_CALLS")
if calls:
    with open(calls, "a", encoding="utf-8") as counted:
        counted.write(f"{robot}\n")
with open(answer_file, "w", encoding="utf-8") as answer:
    json.dump(
        {
            "robot": robot,
            "arms": [{"arm": "left", "shoulder": [0, 0, 1], "radius": 1.0}],
        },
        answer,
    )

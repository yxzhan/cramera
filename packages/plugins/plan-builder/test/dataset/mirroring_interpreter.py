#!/usr/bin/env python3
"""
Stands in for the interpreter running cramera-bridge's posture mirror: called as
``<interpreter> -m <module>``, it reports the posture of a robot whose left shoulder
stands turned, its left arm's driver having reported just now and its right arm's
never, then waits until its standard input closes.
"""

import json
import sys

print(
    json.dumps(
        {
            "jointPositions": {"left_shoulder_pan_joint": 0.3},
            "arms": [{"arm": "left_arm", "ageSeconds": 0.0}, {"arm": "right_arm"}],
        }
    ),
    flush=True,
)
sys.stdin.read()

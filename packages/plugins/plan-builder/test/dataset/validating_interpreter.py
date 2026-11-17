#!/usr/bin/env python3
"""
Stands in for the interpreter running cramera-bridge's validator: called as
``<interpreter> -m <module> <order file> <report file>``, once its input closes, it
writes a report in which every step of the plan's selected robot holds, unless the
plan's task names the step that fails as unreachable.
"""

import json
import sys

_, _, order_file, report_file = sys.argv[1:]
sys.stdin.read()
with open(order_file, encoding="utf-8") as order:
    plan = json.load(order)["demo"]["plan"]
[robot] = [robot for robot in plan["robots"] if robot["id"] == plan["selectedRobot"]]
steps = []
for step in robot["steps"]:
    if step["id"] == plan.get("task"):
        steps.append({"step": step["id"], "verdict": "failed", "reason": "unreachable"})
    else:
        steps.append({"step": step["id"], "verdict": "valid"})
with open(report_file, "w", encoding="utf-8") as report:
    json.dump({"steps": steps}, report)

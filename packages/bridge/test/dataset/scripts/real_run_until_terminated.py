"""
Performs the plan its argument holds, as JSON, in a run on the real robot, printing the
process identities of the processes the run started and a line once the arms start
moving, and runs until it ends or is asked to end.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cramera_bridge.execution import RealExecution
from cramera_bridge.plans.demo import PlannedTaskDemo
from cramera_protocol.plan_builder import DemoDocument, PlanDocument

# %% Process

MOVING = "moving"
"""
The line printed once the arms start moving.
"""

if __name__ == "__main__":
    plan = PlanDocument.from_json(json.loads(sys.argv[1]))
    demo = PlannedTaskDemo.of_document(DemoDocument(plan=plan), port=0)
    demo.execution = RealExecution()
    with demo.execution.attached():
        children = [
            child
            for task in Path("/proc/self/task").iterdir()
            for child in (task / "children").read_text().split()
        ]
        print(*children, flush=True)
        world = demo.build_world()
        context = demo.build_context(world)
        print(MOVING, flush=True)
        demo.performance(context).perform(demo.build_plan(context), context)

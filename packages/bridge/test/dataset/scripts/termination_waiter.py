"""
Waits, as a run on the real robot does, for a request to end, printing a line once it
waits; started with the stop signals blocked, as the plan builder's viewer started runs.
"""

import time

from cramera_bridge.execution import RealExecution

WAITING = "waiting"
"""
The line printed once the process waits for its end.
"""

if __name__ == "__main__":
    with RealExecution().ended_by_termination():
        print(WAITING, flush=True)
        time.sleep(60)

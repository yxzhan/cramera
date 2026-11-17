"""
Starts a Giskard that simulates Tracy, prints the process identity of its Giskard and
waits until it is killed.
"""

import threading

from cramera_bridge.real_robot import GiskardScript, GiskardProcess

if __name__ == "__main__":
    with GiskardProcess(GiskardScript.STANDALONE).running() as giskard:
        print(giskard.pid, flush=True)
        threading.Event().wait()

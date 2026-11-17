"""
A stand-in for the plan builder's halt of the real robot's arms: it says it is ready,
waits for its input to close, then writes into the file its first argument names
whether a stand-in Giskard was still running at that moment, and answers as its second
argument says: ``acknowledged`` or anything else.
"""

import subprocess
import sys
from pathlib import Path

from cramera_protocol.plan_builder import ArmHaltLine

STAND_IN_GISKARD = "stand-in-giskard"
"""
An argument of every stand-in Giskard's process.
"""

if __name__ == "__main__":
    report, answer = Path(sys.argv[1]), sys.argv[2]
    print(ArmHaltLine.READY.value, flush=True)
    sys.stdin.read()
    running = subprocess.run(["pgrep", "-f", STAND_IN_GISKARD]).returncode == 0
    report.write_text("giskard running" if running else "giskard ended")
    acknowledged = answer == "acknowledged"
    print(
        (ArmHaltLine.HALTED if acknowledged else ArmHaltLine.NOT_HALTED).value,
        flush=True,
    )
    sys.exit(0 if acknowledged else 1)

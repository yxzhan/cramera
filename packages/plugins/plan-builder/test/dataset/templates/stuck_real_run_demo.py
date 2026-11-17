"""
A stand-in for the generated demo of ${name} (${file_name}) run on the real robot, as
the plan builder's viewer started runs until a run ignored its Stop: it blocks SIGTERM,
and its stand-in Giskard, in a session of its own, ignores being interrupted, so both
have to be killed.
"""

import signal
import subprocess
import sys
import threading

DEMO = r"""
${demo}
"""

GISKARD = (
    "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
    "time.sleep(600)"
)

if __name__ == "__main__":
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    giskard = subprocess.Popen(
        [sys.executable, "-c", GISKARD, "stand-in-giskard"],
        start_new_session=True,
    )
    print("giskard", giskard.pid, flush=True)
    threading.Event().wait()

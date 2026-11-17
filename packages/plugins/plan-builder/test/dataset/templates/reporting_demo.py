"""
A stand-in for the generated demo of ${name} (${file_name}) that reports run events
between lines of other output, one of them split across two writes, and ends.
"""

import sys
import time

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print("building the world", flush=True)
    print(
        '[run event] {"kind": "step", "step": "step_1", "state": "pending"}', flush=True
    )
    sys.stdout.write('[run event] {"kind": "step", "step": "step_1", ')
    sys.stdout.flush()
    time.sleep(0.2)
    print('"state": "done", "attempt": 1, "attempts": 3}', flush=True)
    print("[run log] step_1: attempt 1/3 succeeded", flush=True)

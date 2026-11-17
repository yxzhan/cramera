"""
A stand-in for the generated demo of ${name} (${file_name}) whose run is over and which
saves the run's record until it is stopped.
"""

import signal
import sys
import threading

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    ended = threading.Event()
    signal.signal(signal.SIGTERM, lambda number, frame: ended.set())
    print('[run event] {"kind": "recordSaving"}', flush=True)
    ended.wait()
    sys.exit(1)

"""
A stand-in for the generated demo of ${name} (${file_name}) that runs in every mode
until its owner goes away or it is stopped.
"""

import json
import signal
import sys
import threading

from cramera.lifeline import Lifeline

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print(
        "plan", json.loads(DEMO)["plan"]["name"], "arguments", *sys.argv[1:], flush=True
    )
    ended = threading.Event()
    signal.signal(signal.SIGTERM, lambda number, frame: ended.set())
    Lifeline(sys.stdin.fileno(), end=ended.set).watch()
    ended.wait()
    print("run ended", flush=True)

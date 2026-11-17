"""
A stand-in for the generated demo of ${name} (${file_name}) whose run is over and which
saves the run's record for a second, then finishes; ended by SIGTERM, it fails.
"""

import sys
import time

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print('[run event] {"kind": "recordSaving"}', flush=True)
    time.sleep(1.0)
    print("record saved", flush=True)

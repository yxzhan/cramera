"""
A stand-in for the generated demo of ${name} (${file_name}) whose run is over and whose
record cannot be saved.
"""

import sys

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print('[run event] {"kind": "recordSaving"}', flush=True)
    print("OSError: [Errno 28] No space left on device", flush=True)
    sys.exit(1)

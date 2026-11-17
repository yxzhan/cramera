"""
A stand-in for the generated demo of ${name} (${file_name}) whose motion broke a safety
limit.
"""

import sys

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print("[safety stop] Left arm workspace", flush=True)
    print("Traceback (most recent call last):", flush=True)
    sys.exit(1)

"""
A stand-in of a demo: prints its arguments, its directory and what was prepared, and
ends with the status its first argument names, its last line left unflushed.
"""

import os
import sys

if __name__ == "__main__":
    print(sys.argv[1:], flush=True)
    print(os.getcwd(), flush=True)
    print(os.environ.get("PREPARED_DEMO_STAND_IN"), flush=True)
    print("unflushed end")
    if sys.argv[1] != "0":
        sys.exit(int(sys.argv[1]))

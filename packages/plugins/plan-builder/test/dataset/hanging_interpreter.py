#!/usr/bin/env python3
"""
Stands in for the interpreter running a cramera-bridge command that never answers, as a
scanner waiting on a perception that does not reply: it says what it waits for and
sleeps.
"""

import time

print("waiting for the perception", flush=True)
time.sleep(60)

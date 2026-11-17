#!/usr/bin/env python3
"""
Stands in for the interpreter running cramera-bridge's posture mirror when the mirror
never reports: called as ``<interpreter> -m <module>``, it waits until its standard
input closes.
"""

import sys

sys.stdin.read()

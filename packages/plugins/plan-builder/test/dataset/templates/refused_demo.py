"""
A stand-in for the generated demo of ${name} (${file_name}) whose robot's check refused
the run before anything moved.
"""

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print(
        "[run] The real robot is not ready: the arm watchdog is not running.",
        flush=True,
    )
    print(
        '[run event] {"kind": "refused", "reason": "The real robot is not ready: the arm '
        'watchdog is not running."}',
        flush=True,
    )

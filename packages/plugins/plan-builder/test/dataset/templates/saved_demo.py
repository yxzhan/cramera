"""
A stand-in for the generated demo of ${name} (${file_name}) whose run is over and whose
record is saved.
"""

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    print('[run event] {"kind": "recordSaving"}', flush=True)

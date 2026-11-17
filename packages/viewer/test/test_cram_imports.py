"""
The gate that lets one thread of the viewer import CRAM at a time.
"""

from __future__ import annotations

import threading

from cramera.cram_imports import CramImports


def test_an_import_waits_while_another_thread_imports_cram():
    imported = threading.Event()
    importing = threading.Thread(
        target=lambda: imported.set() if CramImports().module("json") else None
    )
    with CramImports().lock:
        importing.start()
        assert not imported.wait(0.2)
    assert imported.wait(5.0)
    importing.join()


def test_a_thread_importing_cram_may_import_more_of_it():
    with CramImports().lock:
        assert CramImports().module("json").__name__ == "json"

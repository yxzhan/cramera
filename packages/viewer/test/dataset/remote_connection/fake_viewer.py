"""
Stands in for a viewer started on the far side: it says it serves, as ``cramera`` does,
records that it ended once its standard input closed, and exits.

Called with ``fail``, it ends without serving.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cramera.viewer_options import Announcement

# %% fake viewer


def main(arguments: list[str]) -> None:
    """
    :param arguments: The viewer arguments it was started with.
    """
    if "fail" in arguments:
        print("the port is in use", file=sys.stderr, flush=True)
        sys.exit(1)
    print(f"{Announcement.SERVING} http://127.0.0.1:8711", file=sys.stderr, flush=True)
    print("a later message", file=sys.stderr, flush=True)
    sys.stdin.buffer.read()
    Path(os.environ["FAKE_VIEWER_ENDED"]).write_text(" ".join(arguments))


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
"""
Stands in for ``ssh`` in the tests: it records how it was called and runs the far side's
command on this machine, so the tests need no SSH server.

Opening and closing the shared connection succeed at once; a destination named
``unreachable`` fails as ``ssh`` does. A ``docker exec -i <container>`` prefix is
dropped, so the command runs as it would inside the container.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

# %% fake ssh

CALLS = Path(os.environ["FAKE_SSH_CALLS"])
"""
The file every call is appended to, one line of arguments each.
"""

UNREACHABLE = "unreachable"
"""
The destination the fake cannot connect to.
"""

SSH_FAILURE = 255
"""
The status ``ssh`` exits with when it cannot connect.
"""

DOCKER_PREFIX = ["docker", "exec", "-i"]
"""
The words that run a command inside a container.
"""


def main(arguments: list[str]) -> None:
    """
    :param arguments: The arguments ``ssh`` was called with.
    """
    with CALLS.open("a", encoding="utf-8") as calls:
        calls.write(shlex.join(arguments) + "\n")
    if "-M" in arguments or "-O" in arguments:
        sys.exit(SSH_FAILURE if UNREACHABLE in arguments else 0)
    command = shlex.split(arguments[-1])
    if command[: len(DOCKER_PREFIX)] == DOCKER_PREFIX:
        command = command[len(DOCKER_PREFIX) + 1 :]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])

"""
Joining two byte streams, such as a browser's connection and the standard input and
output of the process that carries it to another machine, so each receives what the
other sends until both have ended.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from typing_extensions import BinaryIO

# %% ends


class ByteEnd(ABC):
    """
    One end of a two-way byte stream.
    """

    @abstractmethod
    def read(self) -> bytes:
        """
        :return: The next bytes that arrived, waiting for some; empty once the other
            side finished sending.
        """

    @abstractmethod
    def write(self, data: bytes) -> None:
        """
        Send all of the bytes.

        :param data: The bytes.
        """

    @abstractmethod
    def finish_writing(self) -> None:
        """
        Tell the other side that nothing more will be sent.
        """


@dataclass(frozen=True)
class SocketEnd(ByteEnd):
    """
    A connected stream socket.
    """

    connection: socket.socket
    """
    The socket.
    """

    read_size: int = 65536
    """
    How many bytes are read at most at once.
    """

    def read(self) -> bytes:
        return self.connection.recv(self.read_size)

    def write(self, data: bytes) -> None:
        self.connection.sendall(data)

    def finish_writing(self) -> None:
        self.connection.shutdown(socket.SHUT_WR)


@dataclass(frozen=True)
class StreamEnd(ByteEnd):
    """
    A pair of binary files, such as a process's standard input and output.
    """

    source: BinaryIO
    """
    The file the bytes arrive from.
    """

    sink: BinaryIO
    """
    The file the bytes are sent to; it is closed when writing finishes.
    """

    read_size: int = 65536
    """
    How many bytes are read at most at once.
    """

    def read(self) -> bytes:
        # read from the descriptor, so bytes are passed on as soon as some arrive
        # instead of once the file object's buffer is full
        return os.read(self.source.fileno(), self.read_size)

    def write(self, data: bytes) -> None:
        self.sink.write(data)
        self.sink.flush()

    def finish_writing(self) -> None:
        self.sink.close()


# %% splice


@dataclass(frozen=True)
class Splice:
    """
    Copies the bytes of each end to the other, each direction until its source ends.
    """

    first: ByteEnd
    """
    One end.
    """

    second: ByteEnd
    """
    The other end.
    """

    def run(self) -> None:
        """
        Copy both directions; returns once both have ended.
        """
        towards_second = threading.Thread(
            target=self.copy, args=(self.first, self.second), daemon=True
        )
        towards_second.start()
        self.copy(self.second, self.first)
        towards_second.join()

    @staticmethod
    def copy(source: ByteEnd, sink: ByteEnd) -> None:
        """
        Copy the bytes of one end to the other until the source ends, then finish
        writing to the sink.

        :param source: The end read from.
        :param sink: The end written to.
        """
        # a side that went away, by resetting its connection or closing its pipe, ends
        # this direction as its regular end would; there is nobody left to tell
        with contextlib.suppress(ConnectionError):
            for data in iter(source.read, b""):
                sink.write(data)
        with contextlib.suppress(OSError):
            sink.finish_writing()

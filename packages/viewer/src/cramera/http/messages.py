"""
HTTP requests and responses as the viewer's routes see them.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from typing_extensions import (
    Any,
    BinaryIO,
    Dict,
    Iterator,
    List,
    Optional,
    Self,
    Type,
    TypeVar,
)

from cramera.exceptions import MalformedRequestBody
from cramera.http.addresses import LOOPBACK
from cramera_protocol.document import JsonDocument, JsonValue
from cramera_protocol.viewer import ErrorKey

DocumentType = TypeVar("DocumentType", bound=JsonDocument)
"""
The protocol document a request body holds.
"""

# %% vocabulary


class HttpMethod(StrEnum):
    """
    The HTTP methods the viewer answers.
    """

    GET = "GET"
    """
    Reads a resource.
    """

    HEAD = "HEAD"
    """
    Reads a resource's headers.
    """

    POST = "POST"
    """
    Asks a route to act.
    """

    OPTIONS = "OPTIONS"
    """
    Asks which cross-origin requests are allowed.
    """

    DELETE = "DELETE"
    """
    Removes a resource.
    """

    def changes(self) -> bool:
        """
        :return: Whether a request of this method may change something, which only
            this machine may ask for.
        """
        return self not in (HttpMethod.GET, HttpMethod.HEAD, HttpMethod.OPTIONS)


class Header(StrEnum):
    """
    The HTTP headers the viewer reads or writes.
    """

    ORIGIN = "Origin"
    """
    The origin of the page that sent the request.
    """

    HOST = "Host"
    """
    The host name the client addressed.
    """

    CONTENT_TYPE = "Content-Type"
    """
    The media type of the body.
    """

    CONTENT_LENGTH = "Content-Length"
    """
    The length of the body in bytes.
    """

    CACHE_CONTROL = "Cache-Control"
    """
    How long the client may keep the answer.
    """

    ALLOW_ORIGIN = "Access-Control-Allow-Origin"
    """
    The origin a cross-origin answer is meant for.
    """

    ALLOW_METHODS = "Access-Control-Allow-Methods"
    """
    The methods a cross-origin request may use.
    """

    ALLOW_HEADERS = "Access-Control-Allow-Headers"
    """
    The headers a cross-origin request may send.
    """

    VARY = "Vary"
    """
    The request headers the answer depends on.
    """

    COOKIE = "Cookie"
    """
    The cookies the client keeps for the viewer.
    """

    SET_COOKIE = "Set-Cookie"
    """
    A cookie the client is to keep.
    """


class CacheDirective(StrEnum):
    """
    The ``Cache-Control`` directives the viewer sends.
    """

    NO_STORE = "no-store"
    """
    The answer must not be kept: scenes and live state change between requests.
    """


class MediaType(StrEnum):
    """
    The media types of the files the viewer serves.
    """

    HTML = "text/html; charset=utf-8"
    """
    Pages.
    """

    JAVASCRIPT = "text/javascript; charset=utf-8"
    """
    Scripts and ES modules.
    """

    CSS = "text/css; charset=utf-8"
    """
    Style sheets.
    """

    JSON = "application/json"
    """
    JSON documents.
    """

    XML = "application/xml"
    """
    URDF and other XML documents.
    """

    TEXT = "text/plain; charset=utf-8"
    """
    Plain text.
    """

    GLB = "model/gltf-binary"
    """
    Binary glTF meshes.
    """

    GLTF = "model/gltf+json"
    """
    GlTF meshes.
    """

    STL = "model/stl"
    """
    STL meshes.
    """

    OBJ = "model/obj"
    """
    Wavefront OBJ meshes and their material libraries.
    """

    COLLADA = "model/vnd.collada+xml"
    """
    COLLADA meshes.
    """

    PNG = "image/png"
    """
    PNG images.
    """

    JPEG = "image/jpeg"
    """
    JPEG images.
    """

    SVG = "image/svg+xml"
    """
    SVG images.
    """

    HDR = "image/vnd.radiance"
    """
    High dynamic range environment images.
    """

    WASM = "application/wasm"
    """
    WebAssembly modules.
    """

    BINARY = "application/octet-stream"
    """
    Anything else.
    """

    @classmethod
    def of_path(cls, path: Path) -> MediaType:
        """
        :param path: A file.
        :return: The media type its suffix names, :attr:`BINARY` when unknown.
        """
        return SUFFIX_MEDIA_TYPES.get(path.suffix.lower(), cls.BINARY)


SUFFIX_MEDIA_TYPES: Dict[str, MediaType] = {
    ".html": MediaType.HTML,
    ".js": MediaType.JAVASCRIPT,
    ".mjs": MediaType.JAVASCRIPT,
    ".css": MediaType.CSS,
    ".json": MediaType.JSON,
    ".urdf": MediaType.XML,
    ".xml": MediaType.XML,
    ".sdf": MediaType.XML,
    ".txt": MediaType.TEXT,
    ".md": MediaType.TEXT,
    ".csv": MediaType.TEXT,
    ".glb": MediaType.GLB,
    ".gltf": MediaType.GLTF,
    ".stl": MediaType.STL,
    ".obj": MediaType.OBJ,
    ".mtl": MediaType.OBJ,
    ".dae": MediaType.COLLADA,
    ".png": MediaType.PNG,
    ".jpg": MediaType.JPEG,
    ".jpeg": MediaType.JPEG,
    ".svg": MediaType.SVG,
    ".hdr": MediaType.HDR,
    ".wasm": MediaType.WASM,
}
"""
The media type of each file suffix the viewer knows.
"""

# %% request

BODY_CHUNK_BYTES = 1024 * 1024
"""
How many bytes of a streamed body are read at once.
"""


@dataclass
class BodyStream:
    """
    A request body read in chunks by the route that answers it, so a large upload never
    has to fit in memory.
    """

    source: BinaryIO
    """
    Where the body's bytes come from.
    """

    length: int
    """
    The body's length in bytes, as the request declared it.
    """

    read_bytes: int = 0
    """
    How many of the body's bytes were read so far.
    """

    def remaining(self) -> int:
        """
        :return: How many of the body's bytes are still unread.
        """
        return self.length - self.read_bytes

    def chunks(self, size: int = BODY_CHUNK_BYTES) -> Iterator[bytes]:
        """
        :param size: The largest chunk, in bytes.
        :return: The unread rest of the body, chunk by chunk; ends early when the
            client stops sending.
        """
        while self.remaining() > 0:
            chunk = self.source.read(min(size, self.remaining()))
            if not chunk:
                return
            self.read_bytes += len(chunk)
            yield chunk

    def read(self) -> bytes:
        """
        :return: The unread rest of the body.
        """
        return b"".join(self.chunks())


@dataclass(frozen=True)
class Request:
    """
    One HTTP request.
    """

    method: HttpMethod
    """
    The request's method.
    """

    path: str
    """
    The path of the request target, without the query.
    """

    query: str = ""
    """
    The query of the request target, without the ``?``.
    """

    headers: Dict[str, str] = field(default_factory=dict)
    """
    The request headers, with their names as the client sent them.
    """

    body: bytes = b""
    """
    The request body, empty when the route reads it from :attr:`stream`.
    """

    stream: Optional[BodyStream] = None
    """
    The unread body of a request to a route that streams its body, see
    :attr:`cramera.http.routing.Route.streams_body`; None when the body was read into
    :attr:`body`.
    """

    server_address: str = LOOPBACK
    """
    The address of this machine the request arrived at.
    """

    client_address: str = LOOPBACK
    """
    The address of the device that sent the request.
    """

    def body_stream(self) -> BodyStream:
        """
        :return: The body as a stream: :attr:`stream`, or one over :attr:`body`.
        """
        if self.stream is not None:
            return self.stream
        return BodyStream(source=io.BytesIO(self.body), length=len(self.body))

    def header(self, name: Header) -> Optional[str]:
        """
        :param name: A header name.
        :return: The header's value, compared without regard to case, or None.
        """
        wanted = name.lower()
        return next(
            (value for key, value in self.headers.items() if key.lower() == wanted),
            None,
        )

    def cookie(self, name: str) -> Optional[str]:
        """
        :param name: A cookie's name.
        :return: The value of the cookie of that name the request carries, or None.
        """
        for pair in (self.header(Header.COOKIE) or "").split(";"):
            key, separator, value = pair.strip().partition("=")
            if separator and key == name:
                return value
        return None

    def parameters(self) -> Dict[str, List[str]]:
        """
        :return: The query parameters, each with every value it was given.
        """
        return parse_qs(self.query)

    def json(self) -> Any:
        """
        :return: The decoded JSON body.
        """
        return json.loads(self.body.decode("utf-8"))

    def document(self, document_class: Type[DocumentType]) -> DocumentType:
        """
        :param document_class: The protocol document the body holds.
        :return: The body read as that document.
        :raises MalformedRequestBody: When the body is not UTF-8 JSON.
        :raises ProtocolError: When the JSON does not describe such a document.
        """
        # the one place where a body that is no JSON at all becomes a refused request
        try:
            data = self.json()
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MalformedRequestBody(str(error)) from error
        return document_class.from_json(data)


# %% response


@dataclass
class Response:
    """
    One HTTP response.
    """

    status: HTTPStatus = HTTPStatus.OK
    """
    The response status.
    """

    body: bytes = b""
    """
    The response body.
    """

    media_type: MediaType = MediaType.TEXT
    """
    The media type of the body.
    """

    headers: Dict[str, str] = field(default_factory=dict)
    """
    Further headers.
    """

    @classmethod
    def of_json(cls, value: Any, status: HTTPStatus = HTTPStatus.OK) -> Self:
        """
        :param value: A protocol value or a JSON-serializable value.
        :param status: The response status.
        :return: The value as a JSON response.
        """
        data = value.to_json() if isinstance(value, JsonValue) else value
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return cls(status=status, body=body, media_type=MediaType.JSON)

    @classmethod
    def of_text(cls, text: str, media_type: MediaType = MediaType.TEXT) -> Self:
        """
        :param text: The body text.
        :param media_type: The body's media type.
        :return: The text as a response.
        """
        return cls(body=text.encode("utf-8"), media_type=media_type)

    @classmethod
    def of_file(cls, path: Path) -> Self:
        """
        :param path: A file the caller has checked may be served.
        :return: The file's content with its media type.
        """
        return cls(body=path.read_bytes(), media_type=MediaType.of_path(path))

    @classmethod
    def of_error(
        cls, status: HTTPStatus, message: str, output: str | None = None
    ) -> Self:
        """
        :param status: The error status.
        :param message: The reason, for the person reading the error.
        :param output: The whole output of the process behind the error, if any.
        :return: The error as a JSON response ``{"error": message}``, with
            ``"output"`` when an output is given.
        """
        body = {ErrorKey.ERROR: message}
        if output is not None:
            body[ErrorKey.OUTPUT] = output
        return cls.of_json(body, status)

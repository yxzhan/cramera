"""Share one authenticated laboratory through an independently managed HTTPS tunnel."""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
from argparse import ArgumentParser
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from typing_extensions import ClassVar

from cramera import paths
from cramera.laboratory_robot_session import RobotPhysicsRoute
from cramera.offline_assets import (
    AssetDependencies,
    AssetReference,
    OfflineSceneAssets,
    SceneAssetField,
)


# %% shared configuration
class ShareRoute(StrEnum):
    """Public entry points of the fixed laboratory."""

    ROOT = "/"
    """Canonical viewer redirect or unauthenticated login form."""
    LOGIN = "/login"
    """Password form submission."""
    INDEX = "/index.html"
    """Packaged viewer document."""
    VIEWER = "/index.html?scene=precision_lab_pr2_physics&layout=scene&offline=1"
    """Laboratory page isolated from the general live bridge."""
    SCENE_INDEX = "/scenes/index.json"
    """Filtered scene picker containing only the shared laboratory."""


class ShareFile(StrEnum):
    """Packaged files forming the viewer and login page."""

    INDEX = "index.html"
    """Viewer dependency entry point."""
    LOGIN = "laboratory-login.html"
    """Password form served before authentication."""
    PHYSICS_CLIENT = "core/laboratory-physics.js"
    """Browser client containing the laboratory's absolute API routes."""


@dataclass(frozen=True)
class ShareOptions:
    """Loopback listeners and private configuration for one shared laboratory."""

    SCENE_NAME: ClassVar[str] = "precision_lab_pr2_physics"
    """Only scene that the public gateway may expose."""
    MAX_BODY_BYTES: ClassVar[int] = 8192
    """Largest accepted form or simulation command."""
    MAX_RESPONSE_BYTES: ClassVar[int] = 4 * 1024 * 1024
    """Largest simulation JSON response accepted from the local backend."""
    COOKIE_NAME: ClassVar[str] = "__Host-cramera_lab"
    """Host-only secure browser session cookie."""
    LOOPBACK: ClassVar[str] = "127.0.0.1"
    """Private address used for both HTTP listeners."""

    password_file: Path
    """Private file containing the login password."""
    public_origin_file: Path
    """File containing the exact current HTTPS tunnel origin."""
    port: int = 8717
    """Loopback gateway port."""
    backend_port: int = 8716
    """Existing local simulation server port."""
    scene_directory: Path | None = None
    """Optional fixed bundle directory, otherwise resolved from the scene catalog."""
    path_prefix: str = ""
    """Optional public mount path without a trailing slash."""

    def __post_init__(self) -> None:
        """Reject ambiguous mount paths before they can affect public routes."""
        if self.path_prefix and not re.fullmatch(
            r"(?:/[A-Za-z0-9_-]+)+", self.path_prefix
        ):
            raise ValueError(
                "The sharing prefix must contain only canonical path segments"
            )

    def public_path(self, route: str) -> str:
        """Place an internal route beneath the configured public mount."""
        return self.path_prefix + route

    @property
    def backend_origin(self) -> str:
        """Return the fixed local simulation origin."""
        return f"http://{self.LOOPBACK}:{self.backend_port}"

    def public_origin(self) -> str | None:
        """Read the configured HTTPS origin, failing closed during tunnel startup."""
        try:
            origin = self.public_origin_file.read_text().strip()
        except OSError:
            return None
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", origin):
            return None
        return origin

    @classmethod
    def parse(cls, arguments: list[str] | None = None) -> ShareOptions:
        """Parse the private gateway's listener and authentication files."""
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("--port", type=int, default=8717)
        parser.add_argument("--backend-port", type=int, default=8716)
        parser.add_argument("--password-file", type=Path, required=True)
        parser.add_argument("--public-origin-file", type=Path, required=True)
        parser.add_argument("--path-prefix", default="")
        options = parser.parse_args(arguments)
        if not 0 <= options.port <= 65535 or not 1 <= options.backend_port <= 65535:
            parser.error("Listener ports must be valid TCP ports")
        return cls(**vars(options))


# %% exact dependency allowlist
@dataclass
class DocumentDependencies(HTMLParser):
    """Collect linked scripts, styles and images from the viewer document."""

    references: list[str] = field(default_factory=list, init=False)
    """Directly linked local web assets."""

    def __post_init__(self) -> None:
        """Initialize the standard HTML parser."""
        super().__init__()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect scripts, images and linked stylesheets."""
        attributes = dict(attrs)
        if tag in ("script", "img") and attributes.get("src"):
            self.references.append(attributes["src"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.references.append(attributes["href"])


@dataclass(frozen=True)
class SharedAsset:
    """One allowlisted file whose resolved location remains beneath its root."""

    path: Path
    """Original allowlisted pathname."""
    root: Path
    """Containing directory that may be exposed."""

    def read(self) -> bytes | None:
        """Read a still-existing file without following an escaping symlink."""
        resolved = self.path.resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            return None
        return resolved.read_bytes()


@dataclass
class LaboratoryAssets:
    """The exact packaged viewer and one scene's transitive dependencies."""

    scene_directory: Path
    """Bundle containing the shared scene."""
    files: dict[str, SharedAsset] = field(default_factory=dict, init=False)
    """Public request paths mapped to individually validated files."""

    def __post_init__(self) -> None:
        """Build both dependency graphs before opening a listener."""
        self._web_files()
        self._scene_files()

    def _web_files(self) -> None:
        """Allow the viewer's declared scripts, styles and images."""
        root = paths.WEB_ROOT.resolve()
        manifest = root / ShareFile.INDEX
        parser = DocumentDependencies()
        parser.feed(manifest.read_text())
        pending = [AssetReference(manifest, str(ShareFile.INDEX))]
        pending.extend(AssetReference(manifest, ref) for ref in parser.references)
        resolver = OfflineSceneAssets(root)
        while pending:
            reference = pending.pop()
            path = resolver.resolve_reference(reference.source, reference.location)
            if path is None:
                continue
            route = "/" + path.relative_to(root).as_posix()
            if route in self.files:
                continue
            self.files[route] = SharedAsset(path, root)
            if path.suffix == ".css":
                pending.extend(
                    AssetReference(path, match.strip(" \t\"'"))
                    for match in re.findall(r"url\(([^)]+)\)", path.read_text())
                    if not match.strip(" \t\"'").startswith("data:")
                )

    def _scene_files(self) -> None:
        """Allow the scene manifest and all declared model and texture dependencies."""
        root = self.scene_directory.resolve()
        manifest = root / SceneAssetField.SCENE
        document = json.loads(manifest.read_text())
        references = [SceneAssetField.SCENE, document[SceneAssetField.TRAJECTORY]]
        references.extend(
            model[SceneAssetField.URDF]
            for model in document.get(SceneAssetField.MODELS, [])
        )
        references.extend(
            item[key]
            for item in document.get(SceneAssetField.OBJECTS, [])
            for key in (SceneAssetField.MESH, SceneAssetField.MATERIAL)
            if key in item
        )
        pending = [AssetReference(manifest, ref) for ref in references]
        resolver = OfflineSceneAssets(root)
        while pending:
            reference = pending.pop()
            path = resolver.resolve_reference(reference.source, reference.location)
            if path is None:
                continue
            route = (
                f"/scenes/{ShareOptions.SCENE_NAME}/{path.relative_to(root).as_posix()}"
            )
            if route in self.files:
                continue
            self.files[route] = SharedAsset(path, root)
            pending.extend(
                AssetReference(path, ref)
                for ref in AssetDependencies(path).references()
            )

    @staticmethod
    def scene_index() -> bytes:
        """Return only the shared laboratory's scene-picker entry."""
        return json.dumps(
            {
                "default": ShareOptions.SCENE_NAME,
                "scenes": [
                    {
                        "name": ShareOptions.SCENE_NAME,
                        "robot": "pr2",
                        "environment": "Precision Laboratory",
                        "task": "PR2 · Kontaktphysik und Flüssigkeiten",
                    }
                ],
            }
        ).encode()


# %% authenticated HTTP gateway
@dataclass(init=False, eq=False)
class LaboratoryShareServer(ThreadingHTTPServer):
    """Own authentication and allowlisted assets independently of the simulation."""

    options: ShareOptions
    """Fixed ports and private configuration files."""
    password: bytes
    """Login secret loaded from the private file."""
    session: str
    """Random process-local browser session token."""
    assets: LaboratoryAssets
    """Allowed public assets."""
    daemon_threads: ClassVar[bool] = True
    """Discard incomplete connections when shutting down."""

    def __init__(self, options: ShareOptions) -> None:
        """Validate the scene and password before listening on loopback."""
        self.options = options
        self.password = options.password_file.read_bytes().rstrip(b"\r\n")
        if len(self.password) < 20:
            raise ValueError("The sharing password must contain at least 20 bytes")
        self.session = secrets.token_urlsafe(32)
        directory = options.scene_directory or paths.resolve_scene_directory(
            ShareOptions.SCENE_NAME
        )
        if directory is None:
            raise FileNotFoundError("The laboratory scene has not been generated")
        self.assets = LaboratoryAssets(directory)
        super().__init__((ShareOptions.LOOPBACK, options.port), ShareHandler)


class ShareHandler(BaseHTTPRequestHandler):
    """Authenticate requests and expose a fixed viewer and simulation API subset."""

    server: LaboratoryShareServer
    """Gateway configuration and process-local authentication."""
    server_version = "LaboratoryShare"
    """Public server identifier without interpreter details."""
    sys_version = ""
    """Suppress the Python version in response headers."""

    def setup(self) -> None:
        """Bound the lifetime of an incomplete HTTP request."""
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid logging credentials, query strings or simulation polls."""

    def _reply(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Send an uncached response constrained to the tunnel origin."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self' blob:; worker-src blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self) -> str | None:
        """Accept a canonical origin-form path without traversal or ambiguous encoding."""
        parsed = urlsplit(self.path)
        route = unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not route.startswith("/")
            or "\\" in route
            or "%" in route
            or "\x00" in route
            or any(part in (".", "..") for part in route.split("/"))
            or "//" in route
        ):
            return None
        prefix = self.server.options.path_prefix
        if prefix:
            if not route.startswith(prefix + "/"):
                return None
            route = route.removeprefix(prefix)
        if parsed.query and route != ShareRoute.INDEX:
            return None
        return route

    def _public_payload(self, route: str, payload: bytes) -> bytes:
        """Keep form and simulation requests beneath this laboratory's mount."""
        options = self.server.options
        if not options.path_prefix:
            return payload
        if route == ShareFile.LOGIN:
            return payload.replace(
                f'action="{ShareRoute.LOGIN}"'.encode(),
                f'action="{options.public_path(ShareRoute.LOGIN)}"'.encode(),
            )
        if route == "/" + ShareFile.PHYSICS_CLIENT:
            return payload.replace(
                b"'/api/laboratory/", f"'{options.path_prefix}/api/laboratory/".encode()
            )
        return payload

    def _public_origin(self) -> str | None:
        """Require the currently configured public host before serving any response."""
        origin = self.server.options.public_origin()
        if origin is None:
            self._reply(HTTPStatus.SERVICE_UNAVAILABLE, b"Sharing is starting.")
            return None
        if self.headers.get_all("Host") != [urlsplit(origin).netloc]:
            self._reply(HTTPStatus.FORBIDDEN, b"Unknown sharing host.")
            return None
        return origin

    def _authenticated(self) -> bool:
        """Compare the host-only cookie with the random process-local session."""
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        if ShareOptions.COOKIE_NAME not in cookie:
            return False
        return hmac.compare_digest(
            cookie[ShareOptions.COOKIE_NAME].value.encode(),
            self.server.session.encode(),
        )

    def _same_origin(self, origin: str) -> bool:
        """Require an exact public origin for every form or control mutation."""
        if (
            self.headers.get_all("Origin") != [origin]
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            self._reply(HTTPStatus.FORBIDDEN, b"A same-origin request is required.")
            return False
        return True

    def _body(self, content_type: str) -> bytes | None:
        """Read one bounded, explicitly typed request without transfer encodings."""
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding") is not None
            or len(lengths) != 1
            or not lengths[0].isdigit()
            or self.headers.get("Content-Type", "").split(";", 1)[0] != content_type
        ):
            self._reply(HTTPStatus.BAD_REQUEST, b"Invalid request body.")
            return None
        length = int(lengths[0])
        if length > ShareOptions.MAX_BODY_BYTES:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Request is too large.")
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self._reply(HTTPStatus.BAD_REQUEST, b"Incomplete request body.")
            return None
        return body

    def _login(self) -> None:
        """Exchange a correct password for a secure, host-only session cookie."""
        body = self._body("application/x-www-form-urlencoded")
        if body is None:
            return
        try:
            form = parse_qs(body.decode("utf-8"), strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            self._reply(HTTPStatus.BAD_REQUEST, b"Invalid login form.")
            return
        password = form.get("password", [])
        if (
            set(form) != {"password"}
            or len(password) != 1
            or not hmac.compare_digest(password[0].encode(), self.server.password)
        ):
            self._reply(HTTPStatus.UNAUTHORIZED, b"Incorrect password.")
            return
        self._reply(
            HTTPStatus.SEE_OTHER,
            headers={
                "Location": self.server.options.public_path(ShareRoute.VIEWER),
                "Set-Cookie": (
                    f"{ShareOptions.COOKIE_NAME}={self.server.session}; "
                    "Path=/; HttpOnly; SameSite=Strict; Secure"
                ),
            },
        )

    def _proxy(self, route: str, body: bytes | None = None) -> None:
        """Forward only a validated laboratory operation to the fixed local backend."""
        options = self.server.options
        connection = HTTPConnection(
            ShareOptions.LOOPBACK, options.backend_port, timeout=30
        )
        try:
            connection.request(
                self.command,
                route,
                body=body,
                headers={
                    "Origin": options.backend_origin,
                    "Sec-Fetch-Site": "same-origin",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = response.read(ShareOptions.MAX_RESPONSE_BYTES + 1)
            if (
                len(payload) > ShareOptions.MAX_RESPONSE_BYTES
                or 300 <= response.status < 400
                or response.getheader("Content-Type", "").split(";", 1)[0]
                != "application/json"
            ):
                self._reply(HTTPStatus.BAD_GATEWAY, b"Invalid simulation response.")
                return
            self._reply(response.status, payload, "application/json")
        except (OSError, HTTPException):
            self._reply(HTTPStatus.BAD_GATEWAY, b"The simulation is unavailable.")
        finally:
            connection.close()

    def do_GET(self) -> None:
        """Serve the login form, authenticated assets or simulation state."""
        if self._public_origin() is None:
            return
        route = self._route()
        if route is None:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        if not self._authenticated():
            if route in (ShareRoute.ROOT, ShareRoute.INDEX, ShareRoute.LOGIN):
                self._reply(
                    HTTPStatus.OK,
                    self._public_payload(
                        ShareFile.LOGIN, (paths.WEB_ROOT / ShareFile.LOGIN).read_bytes()
                    ),
                    "text/html; charset=utf-8",
                )
            else:
                self._reply(HTTPStatus.UNAUTHORIZED, b"Login required.")
            return
        if route in (ShareRoute.ROOT, ShareRoute.LOGIN) or (
            route == ShareRoute.INDEX
            and self.path != self.server.options.public_path(ShareRoute.VIEWER)
        ):
            self._reply(
                HTTPStatus.SEE_OTHER,
                headers={
                    "Location": self.server.options.public_path(ShareRoute.VIEWER)
                },
            )
            return
        if route == RobotPhysicsRoute.STATE:
            self._proxy(route)
            return
        if route == ShareRoute.SCENE_INDEX:
            self._reply(
                HTTPStatus.OK, self.server.assets.scene_index(), "application/json"
            )
            return
        asset = self.server.assets.files.get(route)
        payload = asset.read() if asset is not None else None
        if payload is None:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        self._reply(
            HTTPStatus.OK,
            self._public_payload(route, payload),
            mimetypes.guess_type(str(asset.path))[0] or "application/octet-stream",
        )

    def do_POST(self) -> None:
        """Accept authenticated same-origin laboratory controls or a login form."""
        origin = self._public_origin()
        if origin is None:
            return
        route = self._route()
        if route is None:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        if route != ShareRoute.LOGIN and not self._authenticated():
            self._reply(HTTPStatus.UNAUTHORIZED, b"Login required.")
            return
        if route != ShareRoute.LOGIN and route not in {
            entry for entry in RobotPhysicsRoute if entry != RobotPhysicsRoute.STATE
        }:
            self._reply(HTTPStatus.NOT_FOUND)
            return
        if not self._same_origin(origin):
            return
        if route == ShareRoute.LOGIN:
            self._login()
            return
        body = self._body("application/json")
        if body is not None:
            self._proxy(route, body)


def make_server(options: ShareOptions) -> LaboratoryShareServer:
    """Create the authenticated loopback gateway without changing the backend."""
    return LaboratoryShareServer(options)


def main(arguments: list[str] | None = None) -> None:
    """Serve the fixed laboratory until the sharing process is stopped."""
    with make_server(ShareOptions.parse(arguments)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

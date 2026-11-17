"""
Routing, the origin policy and the server.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path

import pytest

from cramera.exceptions import MalformedRequestBody, UnknownScene
from cramera.http import server as server_module
from cramera.http.addresses import (
    EVERY_ADDRESS,
    is_loopback,
    local_host,
    network_address,
    own_address,
    url_of,
)
from cramera.http.messages import (
    Header,
    HttpMethod,
    MediaType,
    Request,
    Response,
)
from cramera.http.origin import KEY_COOKIE, KEY_PARAMETER, OriginPolicy
from cramera.http.routing import ExactRoute, PrefixRoute, Router
from cramera.http.server import (
    HttpServer,
    RequestAnswerer,
    RequestHandler,
    ViewerHttpServer,
)
from cramera_protocol.bundle.index import SceneIndex
from cramera_protocol.exceptions import MissingDocumentField
from cramera_protocol.viewer import ErrorKey, ViewerRoute

LOCAL_HOST = {Header.HOST: "127.0.0.1:8711"}
"""
The headers of a request addressed to this machine.
"""

NETWORK_ADDRESS = "192.0.2.7"
"""
The network address of the machine serving the viewer in the tests.
"""

PHONE_ADDRESS = "192.0.2.50"
"""
The address of another device of that network.
"""

KEY = "s3cret-Key_1"
"""
The key other devices carry in the tests.
"""

NETWORK_POLICY = OriginPolicy(key=KEY)
"""
The policy of a viewer that other devices of the network may watch.
"""


def request(method=HttpMethod.GET, path="/", **headers) -> Request:
    return Request(method=method, path=path, headers={**LOCAL_HOST, **headers})


def network_request(
    method=HttpMethod.GET, client_address=PHONE_ADDRESS, query="", **headers
) -> Request:
    """
    :return: A request that arrived at the network address, from a phone that keeps
        the key in its cookie by default.
    """
    return Request(
        method=method,
        path="/",
        query=query,
        headers={
            Header.HOST: f"{NETWORK_ADDRESS}:8711",
            Header.COOKIE: f"theme=dark; {KEY_COOKIE}={KEY}",
            **headers,
        },
        server_address=NETWORK_ADDRESS,
        client_address=client_address,
    )


# %% router


def test_the_router_answers_with_the_first_matching_route():
    router = Router()
    router.add(ExactRoute(HttpMethod.GET, "/a", lambda _: Response.of_text("exact")))
    router.add(PrefixRoute(HttpMethod.GET, "/", lambda _, rest: Response.of_text(rest)))
    assert router.answer(request(path="/a")).body == b"exact"
    assert router.answer(request(path="/b/c")).body == b"b/c"


def test_a_head_request_is_answered_by_the_get_route():
    router = Router([ExactRoute(HttpMethod.GET, "/a", lambda _: Response.of_text("x"))])
    assert router.answer(request(HttpMethod.HEAD, "/a")).status is HTTPStatus.OK


def test_an_unknown_path_is_not_found_and_a_wrong_method_not_allowed():
    router = Router([ExactRoute(HttpMethod.GET, "/a", lambda _: Response.of_text("x"))])
    assert router.answer(request(path="/b")).status is HTTPStatus.NOT_FOUND
    assert (
        router.answer(request(HttpMethod.POST, "/a")).status
        is HTTPStatus.METHOD_NOT_ALLOWED
    )


# %% messages


def test_a_request_reads_headers_without_regard_to_case_and_parses_its_query():
    incoming = Request(
        method=HttpMethod.GET,
        path="/",
        query="scene=cell&scene=two",
        headers={"oRiGiN": "x"},
    )
    assert incoming.header(Header.ORIGIN) == "x"
    assert incoming.header(Header.HOST) is None
    assert incoming.parameters() == {"scene": ["cell", "two"]}


REQUESTS = Path(__file__).parent / "dataset" / "requests"
"""
The request bodies.
"""


def posted(file_name):
    return Request(
        method=HttpMethod.POST, path="/", body=(REQUESTS / file_name).read_bytes()
    )


def test_a_request_reads_its_body_as_a_protocol_document():
    index = posted("scene_index.json").document(SceneIndex)
    expected = json.loads((REQUESTS / "scene_index.json").read_text(encoding="utf-8"))
    assert index == SceneIndex.from_json(expected)


@pytest.mark.parametrize("file_name", ["truncated.json", "not_utf8.json"])
def test_a_body_that_is_not_json_is_a_malformed_request(file_name):
    with pytest.raises(MalformedRequestBody) as refused:
        posted(file_name).document(SceneIndex)
    assert refused.value.status() is HTTPStatus.BAD_REQUEST


def test_a_json_response_serializes_protocol_values():
    response = Response.of_json({"a": 1})
    assert json.loads(response.body) == {"a": 1}
    assert response.media_type is MediaType.JSON


def test_media_types_follow_file_suffixes(tmp_path):
    assert MediaType.of_path(tmp_path / "m.GLB") is MediaType.GLB
    assert MediaType.of_path(tmp_path / "robot.urdf") is MediaType.XML
    assert MediaType.of_path(tmp_path / "data.unknown") is MediaType.BINARY


# %% origin policy


@pytest.mark.parametrize(
    "origin", ["http://localhost:8711", "https://127.0.0.1", "http://[::1]:9"]
)
def test_local_origins_are_answered_and_named_in_the_answer(origin):
    policy = OriginPolicy()
    incoming = request(**{Header.ORIGIN: origin})
    assert policy.refusal(incoming) is None
    answered = policy.addressed(incoming, Response())
    assert answered.headers[Header.ALLOW_ORIGIN] == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost.evil.example",
        "file://",
        "null",
        "ftp://localhost",
    ],
)
def test_other_origins_are_refused(origin):
    refusal = OriginPolicy().refusal(request(**{Header.ORIGIN: origin}))
    assert refusal.status is HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("headers", [{Header.HOST: "evil.example:8711"}, {}])
def test_requests_addressed_to_other_hosts_are_refused(headers):
    incoming = Request(method=HttpMethod.GET, path="/", headers=headers)
    assert OriginPolicy().refusal(incoming).status is HTTPStatus.FORBIDDEN


def test_a_page_served_from_the_address_a_request_arrived_at_is_answered():
    origin = {Header.ORIGIN: f"http://{NETWORK_ADDRESS}:8711"}
    assert NETWORK_POLICY.refusal(network_request(**origin)) is None


@pytest.mark.parametrize(
    "headers",
    [
        {Header.HOST: "evil.example:8711"},
        {Header.ORIGIN: "https://evil.example"},
        {Header.ORIGIN: f"http://{PHONE_ADDRESS}:8711"},
    ],
)
def test_other_names_and_pages_of_other_devices_stay_refused_on_the_network(headers):
    refusal = NETWORK_POLICY.refusal(network_request(**headers))
    assert refusal.status is HTTPStatus.FORBIDDEN


@pytest.mark.parametrize(
    "policy, cookie",
    [
        (NETWORK_POLICY, ""),
        (NETWORK_POLICY, f"{KEY_COOKIE}=wrong"),
        (NETWORK_POLICY, f"other_{KEY_COOKIE}={KEY}"),
        (OriginPolicy(), f"{KEY_COOKIE}={KEY}"),
        (OriginPolicy(key=""), f"{KEY_COOKIE}="),
    ],
)
def test_another_device_without_the_key_is_refused(policy, cookie):
    refusal = policy.refusal(network_request(**{Header.COOKIE: cookie}))
    assert refusal.status is HTTPStatus.FORBIDDEN
    assert "key" in json.loads(refusal.body)[ErrorKey.ERROR]


def test_the_key_in_the_query_admits_another_device_and_is_kept_in_a_cookie():
    incoming = network_request(
        query=f"scene=lab&{KEY_PARAMETER}={KEY}", **{Header.COOKIE: ""}
    )
    assert NETWORK_POLICY.refusal(incoming) is None
    cookie = NETWORK_POLICY.addressed(incoming, Response()).headers[Header.SET_COOKIE]
    assert cookie.startswith(f"{KEY_COOKIE}={KEY};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_an_answer_without_the_key_in_its_query_sets_no_cookie():
    answered = NETWORK_POLICY.addressed(
        network_request(query=f"{KEY_PARAMETER}=wrong"), Response()
    )
    assert Header.SET_COOKIE not in answered.headers


@pytest.mark.parametrize(
    "method", [HttpMethod.GET, HttpMethod.HEAD, HttpMethod.OPTIONS]
)
def test_another_device_with_the_key_may_read(method):
    assert NETWORK_POLICY.refusal(network_request(method)) is None


@pytest.mark.parametrize("method", [HttpMethod.POST, HttpMethod.DELETE])
def test_another_device_may_not_change_anything(method):
    refusal = NETWORK_POLICY.refusal(network_request(method))
    assert refusal.status is HTTPStatus.FORBIDDEN
    assert "only watch" in json.loads(refusal.body)[ErrorKey.ERROR]


@pytest.mark.parametrize("client_address", [NETWORK_ADDRESS, "127.0.0.1", "::1"])
def test_this_machine_needs_no_key_and_may_change_things(client_address):
    incoming = network_request(
        HttpMethod.POST, client_address=client_address, **{Header.COOKIE: ""}
    )
    assert NETWORK_POLICY.refusal(incoming) is None


def test_a_request_reads_its_cookies():
    incoming = request(**{Header.COOKIE: "a=1; b=x=y;c"})
    assert (incoming.cookie("a"), incoming.cookie("b"), incoming.cookie("c")) == (
        "1",
        "x=y",
        None,
    )


def test_a_preflight_allows_the_viewer_methods():
    answer = OriginPolicy().preflight(
        request(HttpMethod.OPTIONS, **{Header.ORIGIN: "http://localhost"})
    )
    assert answer.status is HTTPStatus.NO_CONTENT
    assert HttpMethod.POST.value in answer.headers[Header.ALLOW_METHODS]


# %% answerer


def test_viewer_and_protocol_errors_become_error_answers():
    def unknown(_):
        raise UnknownScene("x")

    def malformed(_):
        raise MissingDocumentField("Scene", "name")

    router = Router(
        [
            ExactRoute(HttpMethod.GET, "/u", unknown),
            ExactRoute(HttpMethod.GET, "/m", malformed),
        ]
    )
    answerer = RequestAnswerer(router=router, policy=OriginPolicy())
    unknown_answer = answerer.answer(request(path="/u"))
    assert unknown_answer.status is HTTPStatus.NOT_FOUND
    assert (
        json.loads(unknown_answer.body)[ErrorKey.ERROR] == UnknownScene("x").message()
    )
    assert answerer.answer(request(path="/m")).status is HTTPStatus.BAD_REQUEST


def test_the_answerer_refuses_before_routing():
    answerer = RequestAnswerer(router=Router(), policy=OriginPolicy())
    assert (
        answerer.answer(request(**{Header.ORIGIN: "https://evil.example"})).status
        is HTTPStatus.FORBIDDEN
    )
    preflight = request(HttpMethod.OPTIONS, **{Header.ORIGIN: "http://localhost"})
    assert answerer.answer(preflight).status is HTTPStatus.NO_CONTENT


# %% server


def test_the_server_answers_over_a_socket_and_stops(answerer):
    with HttpServer(answerer=answerer) as server:
        with urllib.request.urlopen(server.url() + ViewerRoute.INFO) as answer:
            assert answer.status == HTTPStatus.OK
        head = urllib.request.Request(
            server.url() + ViewerRoute.INFO, method=HttpMethod.HEAD
        )
        with urllib.request.urlopen(head) as answer:
            assert answer.read() == b""
        posted = urllib.request.Request(
            server.url() + ViewerRoute.SCENES, method=HttpMethod.POST, data=b"{}"
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(posted)
        assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert server.server is None
    server.stop()


def test_the_server_releases_its_routes_once_it_stopped_serving(answerer):
    serving_when_released = []
    server = HttpServer(answerer=answerer)
    server.on_stop.append(lambda: serving_when_released.append(server.server))
    with server:
        assert serving_when_released == []
    assert serving_when_released == [None]


def test_the_server_decodes_percent_encoded_paths(answerer):
    with HttpServer(answerer=answerer) as server:
        url = server.url() + ViewerRoute.SCENE_FILES + "cell/meshes/arm%20base.stl"
        with urllib.request.urlopen(url) as answer:
            assert answer.read().startswith(b"solid")
        escaping = (
            server.url()
            + ViewerRoute.SCENE_FILES
            + "cell/..%2F..%2Fshared_scenes%2Fcell%2Fscene.json"
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(escaping)
        assert refused.value.code == HTTPStatus.FORBIDDEN


def test_the_server_answers_a_page_that_asks_for_hundreds_of_files_at_once(answerer):
    with HttpServer(answerer=answerer) as server:
        url = server.url() + ViewerRoute.SCENE_FILES + "cell/meshes/arm%20base.stl"

        def status(_: int) -> int:
            with urllib.request.urlopen(url, timeout=2) as answer:
                return answer.status

        with ThreadPoolExecutor(max_workers=250) as pool:
            assert set(pool.map(status, range(250))) == {HTTPStatus.OK}


def test_the_url_brackets_an_ipv6_host(answerer):
    server = HttpServer(answerer=answerer, host="::1")
    with server:
        assert server.url().startswith("http://[::1]:")


# %% streamed bodies


def counted(request: Request) -> Response:
    """
    :return: The number of bytes of the streamed body and of its read chunks.
    """
    chunks = list(request.body_stream().chunks(size=4))
    return Response.of_text(
        f"{len(request.body)} {sum(map(len, chunks))} {len(chunks)}"
    )


def first_byte(request: Request) -> Response:
    """
    :return: The first byte of the body, leaving the rest unread.
    """
    return Response(body=next(request.body_stream().chunks(size=1)))


def streaming_answerer() -> RequestAnswerer:
    """
    :return: Answers a streaming route, a streaming route that stops reading early and a
        route that has its body read for it.
    """
    router = Router(
        [
            ExactRoute(HttpMethod.POST, "/streamed", counted, streams_body=True),
            ExactRoute(HttpMethod.POST, "/early", first_byte, streams_body=True),
            ExactRoute(HttpMethod.POST, "/read", counted),
        ]
    )
    return RequestAnswerer(router=router, policy=OriginPolicy())


def test_the_router_names_the_routes_that_stream_their_body():
    router = streaming_answerer().router
    assert router.streams_body(HttpMethod.POST, "/streamed")
    assert not router.streams_body(HttpMethod.POST, "/read")
    assert not router.streams_body(HttpMethod.GET, "/streamed")
    assert not router.streams_body(HttpMethod.POST, "/elsewhere")


def test_a_body_read_into_memory_is_streamed_from_there():
    stream = Request(method=HttpMethod.POST, path="/", body=b"0123456789").body_stream()
    assert [len(chunk) for chunk in stream.chunks(size=4)] == [4, 4, 2]
    assert stream.remaining() == 0
    assert stream.read() == b""


def test_a_streaming_route_reads_the_body_from_the_socket():
    with HttpServer(answerer=streaming_answerer()) as server:
        for route, expected in (("/streamed", b"0 10 3"), ("/read", b"10 10 3")):
            posted = urllib.request.Request(
                server.url() + route, method=HttpMethod.POST, data=b"0123456789"
            )
            with urllib.request.urlopen(posted) as answer:
                assert answer.read() == expected


def test_a_body_left_unread_ends_the_connection_after_the_answer():
    with HttpServer(answerer=streaming_answerer()) as server:
        host, port = server.server.server_address[:2]
        with socket.create_connection((host, port)) as connection:
            connection.sendall(
                b"POST /early HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n\r\n"
                b"0123456789"
            )
            received = b""
            while chunk := connection.recv(4096):
                received += chunk
        assert received.startswith(b"HTTP/1.1 200")
        assert received.endswith(b"\r\n\r\n0")


# %% dropped connections


def test_a_client_that_drops_its_connection_is_no_server_error(capsys):
    server = ViewerHttpServer(("127.0.0.1", 0), RequestHandler)
    try:
        raise BrokenPipeError("the client went away")
    except BrokenPipeError:
        server.handle_error(None, ("127.0.0.1", 1))
    server.server_close()
    assert capsys.readouterr().err == ""


def test_other_errors_while_answering_are_still_reported(capsys):
    server = ViewerHttpServer(("127.0.0.1", 0), RequestHandler)
    try:
        raise ValueError("a real fault")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 1))
    server.server_close()
    assert "ValueError: a real fault" in capsys.readouterr().err


def test_a_server_on_this_machine_only_has_no_network_url(answerer):
    with HttpServer(answerer=answerer) as server:
        assert server.network_url() is None


def test_a_server_on_every_address_is_reached_here_and_from_the_network(
    answerer, monkeypatch
):
    monkeypatch.setattr(server_module, "network_address", lambda: NETWORK_ADDRESS)
    with HttpServer(answerer=answerer, host=EVERY_ADDRESS) as server:
        assert server.url() == url_of("127.0.0.1", server.bound_port())
        assert server.network_url() == url_of(NETWORK_ADDRESS, server.bound_port())
        with urllib.request.urlopen(server.url() + ViewerRoute.INFO) as answer:
            assert answer.status == HTTPStatus.OK


def test_a_server_on_every_address_without_a_network_has_no_network_url(
    answerer, monkeypatch
):
    monkeypatch.setattr(server_module, "network_address", lambda: None)
    with HttpServer(answerer=answerer, host=EVERY_ADDRESS) as server:
        assert server.network_url() is None


def test_the_server_answers_this_machine_through_its_network_address(answerer):
    address = network_address()
    if address is None:
        pytest.skip("this machine has no network address")
    with HttpServer(answerer=answerer, host=EVERY_ADDRESS) as server:
        url = url_of(address, server.bound_port())
        with urllib.request.urlopen(url + ViewerRoute.INFO) as answer:
            assert answer.status == HTTPStatus.OK
        posted = urllib.request.Request(
            url + ViewerRoute.SCENES, method=HttpMethod.POST, data=b"{}"
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(posted)
        # routed, so not refused as another device's change
        assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED


@pytest.mark.parametrize(
    "address, own",
    [("::ffff:192.0.2.7", "192.0.2.7"), ("192.0.2.7", "192.0.2.7"), ("::1", "::1")],
)
def test_an_ipv4_address_mapped_into_ipv6_is_the_ipv4_address(address, own):
    assert own_address(address) == own


def test_loopback_addresses_are_this_machine():
    assert is_loopback("127.0.0.1") and is_loopback("::1")
    assert is_loopback("::ffff:127.0.0.1") and not is_loopback(NETWORK_ADDRESS)


@pytest.mark.parametrize(
    "host, local",
    [("0.0.0.0", "127.0.0.1"), ("::", "::1"), (NETWORK_ADDRESS, NETWORK_ADDRESS)],
)
def test_a_wildcard_address_is_reached_here_through_loopback(host, local):
    assert local_host(host) == local


def test_an_ipv6_host_gets_an_ipv6_socket():
    server = ViewerHttpServer(("::1", 0), RequestHandler)
    server.server_close()
    assert server.address_family == socket.AF_INET6

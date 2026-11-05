"""
The live relay: what the viewer's page reads from a live publisher on this machine, read
through the viewer's own origin, so another device of the network can follow a run.
"""

from __future__ import annotations

import json
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

import pytest

from cramera.application import ViewerApplication
from cramera.exceptions import LivePublisherUnreachable, UnknownLiveRead
from cramera.http.messages import Header, HttpMethod, MediaType, Request, Response
from cramera.http.origin import KEY_COOKIE, KEY_PARAMETER, OriginPolicy
from cramera.http.routing import ExactRoute, Router
from cramera.http.server import HttpServer, RequestAnswerer, ViewerHttpServer
from cramera.live_relay import (
    RELAYED_CHANGES,
    RELAYED_READS,
    ChangeReach,
    LiveRelay,
)
from cramera_protocol.live import LIVE_HOST, LIVE_PORT, LiveRoute
from cramera_protocol.live_messages import LiveParameter
from cramera_protocol.viewer import ErrorKey, ViewerRoute

NETWORK_ADDRESS = "192.0.2.7"
"""
The network address of the machine serving the viewer in the tests.
"""

PHONE_ADDRESS = "192.0.2.50"
"""
The address of a phone of that network.
"""

KEY = "s3cret-Key_1"
"""
The key the phone carries.
"""

IMAGE = b"\x89PNG\r\n\x1a\nimage"
"""
The perception image the fake publisher serves.
"""

IMAGE_KEY = "image-1"
"""
The key of that image.
"""

# %% a fake publisher


def echo(request: Request) -> Response:
    """
    :param request: A request the fake publisher received.
    :return: What it was asked and which host it was addressed to.
    """
    return Response.of_json(
        {
            "route": request.path,
            "query": request.query,
            "host": request.header(Header.HOST),
            "body": request.body.decode(),
        }
    )


def perception_image(request: Request) -> Response:
    """
    :param request: A request for a perception image.
    :return: The image of :data:`IMAGE_KEY`, and the publisher's own 404 for any other.
    """
    if request.parameters().get(LiveParameter.KEY) != [IMAGE_KEY]:
        return Response.of_error(HTTPStatus.NOT_FOUND, "No image has this key.")
    return Response(body=IMAGE, media_type=MediaType.PNG)


@pytest.fixture(scope="module")
def publisher():
    """
    :return: A publisher on this machine answering every live route, under the origin
        policy a live publisher has; one for every test, since a server takes half a
        second to stop.
    """
    router = Router()
    for route in LiveRoute:
        handler = perception_image if route is LiveRoute.PERCEPTION_IMAGE else echo
        router.add(ExactRoute(HttpMethod.GET, route, handler))
        router.add(ExactRoute(HttpMethod.POST, route, echo))
    server = HttpServer(RequestAnswerer(router=router, policy=OriginPolicy()))
    with server:
        yield server


@pytest.fixture
def relay(publisher) -> LiveRelay:
    """
    :return: The relay of the fake publisher.
    """
    return LiveRelay(port=publisher.bound_port())


@pytest.fixture
def closed_port() -> int:
    """
    :return: A port of this machine nothing listens on.
    """
    with socket.socket() as probe:
        probe.bind((LIVE_HOST, 0))
        return probe.getsockname()[1]


def viewer(catalog, locations, relay, policy=None) -> RequestAnswerer:
    """
    :return: The viewer's answerer with the relay, under the given origin policy.
    """
    application = ViewerApplication(
        catalog=catalog, locations=locations, live_relay=relay
    )
    return RequestAnswerer(
        router=application.router(), policy=policy or OriginPolicy(key=KEY)
    )


def relayed(route: str, method=HttpMethod.GET, query="") -> Request:
    """
    :param route: A live route.
    :return: A request a phone that keeps the key in its cookie sends to the viewer's
        network address for the route through the relay.
    """
    return Request(
        method=method,
        path=f"{ViewerRoute.LIVE_RELAY}{route.lstrip('/')}",
        query=query,
        headers={
            Header.HOST: f"{NETWORK_ADDRESS}:8711",
            Header.COOKIE: f"{KEY_COOKIE}={KEY}",
        },
        server_address=NETWORK_ADDRESS,
        client_address=PHONE_ADDRESS,
    )


# %% reads


def test_the_relay_reads_the_default_publisher_on_this_machine():
    relay = LiveRelay()
    assert (relay.host, relay.port) == (LIVE_HOST, LIVE_PORT)
    assert relay.address() == f"http://{LIVE_HOST}:{LIVE_PORT}"


def test_the_relay_reads_what_the_page_reads_and_nothing_else():
    assert set(RELAYED_READS) == {
        LiveRoute.INFO,
        LiveRoute.STATE,
        LiveRoute.PLAN,
        LiveRoute.STATECHART,
        LiveRoute.RECORDING,
        LiveRoute.LIVE_SCENE,
        LiveRoute.RESOLUTIONS,
        LiveRoute.QUERY_STEPS,
        LiveRoute.DETECTED_EVENTS,
        LiveRoute.PERCEPTION_IMAGE,
    }


@pytest.mark.parametrize(
    "route", sorted(set(RELAYED_READS) - {LiveRoute.PERCEPTION_IMAGE})
)
def test_a_phone_reads_each_observation_through_the_viewer(
    catalog, locations, relay, publisher, route
):
    answer = viewer(catalog, locations, relay).answer(relayed(route))
    assert answer.status is HTTPStatus.OK
    assert answer.media_type is MediaType.JSON
    assert json.loads(answer.body) == {
        "route": route,
        "query": "",
        "host": f"{LIVE_HOST}:{publisher.bound_port()}",
        "body": "",
    }


def test_a_read_carries_only_the_parameters_its_route_takes(relay):
    query = f"{LiveParameter.REVISION}=3&{KEY_PARAMETER}={KEY}&other=x"
    answer = relay.answer(relayed(LiveRoute.QUERY_STEPS, query=query), "query_steps")
    assert json.loads(answer.body)["query"] == f"{LiveParameter.REVISION}=3"
    plain = relay.answer(relayed(LiveRoute.STATE, query=query), "state")
    assert json.loads(plain.body)["query"] == ""


def test_a_perception_image_is_relayed_as_the_image(catalog, locations, relay):
    query = f"{LiveParameter.KEY}={IMAGE_KEY}"
    answer = viewer(catalog, locations, relay).answer(
        relayed(LiveRoute.PERCEPTION_IMAGE, query=query)
    )
    assert answer.status is HTTPStatus.OK
    assert answer.media_type is MediaType.PNG
    assert answer.body == IMAGE
    # the image's key is no viewer key, so the phone's cookie stays as it is
    assert Header.SET_COOKIE not in answer.headers


def test_the_publisher_s_own_refusal_is_relayed_as_it_gave_it(relay):
    answer = relay.answer(
        relayed(LiveRoute.PERCEPTION_IMAGE, query=f"{LiveParameter.KEY}=missing"),
        "perception_image",
    )
    assert answer.status is HTTPStatus.NOT_FOUND
    assert json.loads(answer.body) == {ErrorKey.ERROR: "No image has this key."}


# %% refusals


@pytest.mark.parametrize(
    "path",
    [
        "eql",
        "question",
        "recording/stop",
        "recording/save",
        "transforms",
        "mesh",
        "info/../eql",
        "/info",
        "INFO",
        "",
    ],
)
def test_routes_the_page_does_not_read_are_not_relayed(relay, path):
    with pytest.raises(UnknownLiveRead) as refused:
        relay.answer(relayed(f"/{path}"), path)
    assert refused.value.status() is HTTPStatus.NOT_FOUND
    assert repr(path) in refused.value.message()


def test_a_route_that_is_not_relayed_is_not_found_for_the_page(
    catalog, locations, relay
):
    answer = viewer(catalog, locations, relay).answer(relayed("/transforms"))
    assert answer.status is HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "route", [LiveRoute.RECORDING_STOP, LiveRoute.QUERY_STEPS_NEXT, LiveRoute.STATE]
)
def test_a_phone_may_not_change_the_run_through_the_relay(
    catalog, locations, relay, route
):
    answer = viewer(catalog, locations, relay).answer(
        relayed(route, method=HttpMethod.POST)
    )
    assert answer.status is HTTPStatus.FORBIDDEN
    assert "only watch" in json.loads(answer.body)[ErrorKey.ERROR]


def forwarded(route: str, method=HttpMethod.GET, body: bytes = b"") -> Request:
    """
    :param route: A live route.
    :return: A request for the route through the relay from a page whose port a laptop
        forwards to the viewer: the page names the laptop's loopback port, and the
        forwarding arrives from this machine's loopback address.
    """
    return Request(
        method=method,
        path=f"{ViewerRoute.LIVE_RELAY}{route.lstrip('/')}",
        headers={
            Header.HOST: "localhost:18711",
            Header.ORIGIN: "http://localhost:18711",
            Header.CONTENT_TYPE: MediaType.JSON,
        },
        body=body,
    )


@pytest.mark.parametrize("route", sorted(RELAYED_CHANGES))
def test_this_machine_changes_the_run_through_the_relay(
    catalog, locations, relay, route
):
    body = b'{"name": "run"}'
    answer = viewer(catalog, locations, relay, OriginPolicy()).answer(
        forwarded(route, HttpMethod.POST, body)
    )
    assert answer.status is HTTPStatus.OK
    assert json.loads(answer.body)["route"] == route
    assert json.loads(answer.body)["body"] == body.decode()


def test_a_forwarded_page_reads_the_run_through_the_relay(catalog, locations, relay):
    answer = viewer(catalog, locations, relay, OriginPolicy()).answer(
        forwarded(LiveRoute.STATE)
    )
    assert answer.status is HTTPStatus.OK
    assert json.loads(answer.body)["route"] == LiveRoute.STATE


@pytest.mark.parametrize("route", [LiveRoute.QUERY, LiveRoute.MOVE])
def test_this_machine_makes_no_other_change_through_the_relay(
    catalog, locations, relay, route
):
    answer = viewer(catalog, locations, relay, OriginPolicy()).answer(
        forwarded(route, HttpMethod.POST)
    )
    assert answer.status is HTTPStatus.METHOD_NOT_ALLOWED


def test_a_phone_without_the_key_reads_nothing_through_the_relay(
    catalog, locations, relay
):
    request = relayed(LiveRoute.STATE)
    del request.headers[Header.COOKIE]
    answer = viewer(catalog, locations, relay).answer(request)
    assert answer.status is HTTPStatus.FORBIDDEN


# %% control behind a proxy

PROXY_POLICY = OriginPolicy(
    allowed_hosts=frozenset({LIVE_HOST, "localhost", "::1", "cramera.example.org"}),
    proxied=True,
)
"""
The policy of a viewer behind a password-checking proxy on this machine.
"""


def through_proxy(route: str, body: bytes = b"") -> Request:
    """
    :param route: A live route.
    :return: A POST for the route through the relay, as the proxy forwards it.
    """
    return Request(
        method=HttpMethod.POST,
        path=f"{ViewerRoute.LIVE_RELAY}{route.lstrip('/')}",
        headers={
            Header.HOST: "cramera.example.org",
            Header.ORIGIN: "https://cramera.example.org",
            Header.CONTENT_TYPE: MediaType.JSON,
        },
        body=body,
    )


CONTROLLING_DEVICE_CHANGES = sorted(
    route
    for route, reach in RELAYED_CHANGES.items()
    if reach is ChangeReach.CONTROLLING_DEVICES
)
"""
The relayed changes devices that control runs may make.
"""


def test_devices_that_control_runs_may_only_step_the_run():
    assert CONTROLLING_DEVICE_CHANGES == sorted(
        [LiveRoute.QUERY_STEPS_NEXT, LiveRoute.QUERY_STEPS_MODE]
    )


@pytest.mark.parametrize("route", CONTROLLING_DEVICE_CHANGES)
def test_a_page_behind_the_proxy_steps_the_run_through_the_relay(
    catalog, locations, relay, route
):
    body = b'{"stepping": false}'
    answer = viewer(catalog, locations, relay, PROXY_POLICY).answer(
        through_proxy(route, body)
    )
    assert answer.status is HTTPStatus.OK
    assert json.loads(answer.body)["route"] == route
    assert json.loads(answer.body)["body"] == body.decode()


@pytest.mark.parametrize(
    "route", [LiveRoute.QUERY, LiveRoute.RECORDING_SAVE, LiveRoute.MOVE]
)
def test_a_page_behind_the_proxy_changes_nothing_else_through_the_relay(
    catalog, locations, relay, route
):
    answer = viewer(catalog, locations, relay, PROXY_POLICY).answer(
        through_proxy(route)
    )
    assert answer.status is HTTPStatus.FORBIDDEN


def test_a_phone_of_the_network_does_not_step_the_run(catalog, locations, relay):
    answer = viewer(catalog, locations, relay).answer(
        relayed(LiveRoute.QUERY_STEPS_NEXT, method=HttpMethod.POST)
    )
    assert answer.status is HTTPStatus.FORBIDDEN


def test_stepping_without_a_publisher_is_a_service_unavailable(
    catalog, locations, closed_port
):
    relay = LiveRelay(port=closed_port)
    answer = viewer(catalog, locations, relay, PROXY_POLICY).answer(
        through_proxy(LiveRoute.QUERY_STEPS_NEXT, b"{}")
    )
    assert answer.status is HTTPStatus.SERVICE_UNAVAILABLE


# %% no publisher


def test_no_publisher_on_this_machine_is_a_service_unavailable(
    catalog, locations, closed_port
):
    relay = LiveRelay(port=closed_port)
    with pytest.raises(LivePublisherUnreachable) as unreachable:
        relay.answer(relayed(LiveRoute.INFO), "info")
    assert unreachable.value.status() is HTTPStatus.SERVICE_UNAVAILABLE
    answer = viewer(catalog, locations, relay).answer(relayed(LiveRoute.INFO))
    assert answer.status is HTTPStatus.SERVICE_UNAVAILABLE
    error = json.loads(answer.body)[ErrorKey.ERROR]
    assert f"127.0.0.1:{closed_port}" in error
    assert "cramera-live" in error


def test_a_publisher_that_does_not_answer_in_time_is_unavailable():
    with socket.socket() as silent:
        # a listening socket that never accepts: the connection opens, no answer comes
        silent.bind((LIVE_HOST, 0))
        silent.listen()
        relay = LiveRelay(port=silent.getsockname()[1], timeout=0.2)
        with pytest.raises(LivePublisherUnreachable):
            relay.answer(relayed(LiveRoute.STATE), "state")


class OtherService(BaseHTTPRequestHandler):
    """
    A service that is no live publisher, answering in a media type the viewer does not
    serve.
    """

    def do_GET(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header(Header.CONTENT_TYPE, "text/x-other")
        self.send_header(Header.CONTENT_LENGTH, "2")
        self.end_headers()
        self.wfile.write(b"hi")

    def log_message(self, format: str, *arguments: object) -> None:
        pass


def test_another_service_on_the_publisher_s_port_is_not_relayed():
    service = ViewerHttpServer((LIVE_HOST, 0), OtherService)
    with service:
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        relay = LiveRelay(port=service.server_address[1])
        with pytest.raises(LivePublisherUnreachable) as unreachable:
            relay.answer(relayed(LiveRoute.INFO), "info")
        service.shutdown()
    assert "no live publisher" in unreachable.value.message()

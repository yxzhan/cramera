"""
What the viewer's page reads from the live publisher on this machine, relayed through
the viewer's own origin.

The publisher answers this machine only, since a query runs code. The page reads the
publisher at its default address through the viewer, so the viewer's port is the only
one a page has to reach: a phone of the network does, and so does a laptop that forwards
that port from a remote server. The observations the page follows a run by are relayed
for every page the viewer answers; the changes the page makes are relayed for this
machine, and stepping through the run's queries also for devices the viewer lets control
runs.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlencode

from typing_extensions import FrozenSet, List, Mapping, Optional

from cramera.exceptions import LivePublisherUnreachable, UnknownLiveRead
from cramera.http.addresses import url_of
from cramera.http.messages import Header, HttpMethod, MediaType, Request, Response
from cramera.http.routing import ExactRoute, PrefixRoute, Route
from cramera_protocol.live import LIVE_HOST, LIVE_PORT, LiveRoute
from cramera_protocol.live_messages import LiveParameter
from cramera_protocol.viewer import ViewerRoute

RELAYED_READS: Mapping[LiveRoute, FrozenSet[LiveParameter]] = {
    LiveRoute.INFO: frozenset(),
    LiveRoute.STATE: frozenset(),
    LiveRoute.PLAN: frozenset(),
    LiveRoute.STATECHART: frozenset(),
    LiveRoute.RECORDING: frozenset(),
    LiveRoute.LIVE_SCENE: frozenset(),
    LiveRoute.RESOLUTIONS: frozenset(),
    LiveRoute.QUERY_STEPS: frozenset({LiveParameter.REVISION}),
    LiveRoute.DETECTED_EVENTS: frozenset(),
    LiveRoute.PERCEPTION_IMAGE: frozenset({LiveParameter.KEY}),
    LiveRoute.MARKERS: frozenset(),
    LiveRoute.MARKER_TOPICS: frozenset(),
}
"""
The routes the page reads to follow a run, each with the query parameters it takes; no
other route, and no other parameter, is relayed.
"""


class ChangeReach(StrEnum):
    """
    Who may make a change relayed to the publisher.
    """

    THIS_MACHINE = "this_machine"
    """
    Only pages of the machine that serves the viewer.
    """

    CONTROLLING_DEVICES = "controlling_devices"
    """
    Also other devices the viewer lets control runs.
    """


RELAYED_CHANGES: Mapping[LiveRoute, ChangeReach] = {
    LiveRoute.RECORDING_STOP: ChangeReach.THIS_MACHINE,
    LiveRoute.RECORDING_SAVE: ChangeReach.THIS_MACHINE,
    LiveRoute.RECORDING_DISCARD: ChangeReach.THIS_MACHINE,
    LiveRoute.RESOLUTION_EXECUTION: ChangeReach.THIS_MACHINE,
    LiveRoute.MARKER_TOPICS: ChangeReach.THIS_MACHINE,
    LiveRoute.QUERY_STEPS_NEXT: ChangeReach.CONTROLLING_DEVICES,
    LiveRoute.QUERY_STEPS_MODE: ChangeReach.CONTROLLING_DEVICES,
}
"""
The changes the page makes, relayed with their body, each with who may make it: ending,
saving and discarding the recording, executing a resolution and choosing the marker
topics read stay with this machine; letting a held run carry on and choosing whether it
is held at every query are also offered to devices that control runs.
"""

RELAY_TIMEOUT = 30.0
"""
Seconds to wait for the publisher's answer, which includes bundling its world the first
time the page asks for the live scene.
"""

MEDIA_TYPES: Mapping[str, MediaType] = {
    media_type.value: media_type for media_type in MediaType
}
"""
The media types the viewer answers in, by the ``Content-Type`` that names them.
"""

# %% relay


@dataclass(frozen=True)
class LiveRelay:
    """
    Reads the live publisher on this machine for pages that cannot reach it themselves.
    """

    host: str = LIVE_HOST
    """
    The address the publisher listens on, one of this machine's own.
    """

    port: int = LIVE_PORT
    """
    The port the publisher listens on.
    """

    timeout: float = RELAY_TIMEOUT
    """
    Seconds to wait for the publisher's answer.
    """

    def address(self) -> str:
        """
        :return: The publisher's base URL.
        """
        return url_of(self.host, self.port)

    def routes(self) -> List[Route]:
        """
        :return: The viewer's routes of the relay: every read below its prefix, and one
            route for each relayed change, offered to whom :data:`RELAYED_CHANGES` names.
        """
        reads: List[Route] = [
            PrefixRoute(HttpMethod.GET, ViewerRoute.LIVE_RELAY, self.answer)
        ]
        changes = [
            ExactRoute(
                HttpMethod.POST,
                f"{ViewerRoute.LIVE_RELAY}{route.lstrip('/')}",
                self.change,
                remote_changes=reach is ChangeReach.CONTROLLING_DEVICES,
            )
            for route, reach in sorted(RELAYED_CHANGES.items())
        ]
        return reads + changes

    def answer(self, request: Request, route: str) -> Response:
        """
        :param request: A read below the relay's prefix.
        :param route: The publisher's route, relative to the prefix.
        :return: The publisher's answer, as it gave it.
        :raises UnknownLiveRead: When the page does not read the route.
        :raises LivePublisherUnreachable: When no live publisher answers.
        """
        path = f"/{route}"
        if path not in RELAYED_READS:
            raise UnknownLiveRead(route)
        pairs = [
            (name, value)
            for name, values in request.parameters().items()
            if name in RELAYED_READS[path]
            for value in values
        ]
        return self.forward(
            HttpMethod.GET, f"{path}?{urlencode(pairs)}" if pairs else path
        )

    def change(self, request: Request) -> Response:
        """
        :param request: A relayed change, one of :data:`RELAYED_CHANGES` below the
            relay's prefix.
        :return: The publisher's answer, as it gave it.
        :raises LivePublisherUnreachable: When no live publisher answers.
        """
        path = f"/{request.path.removeprefix(ViewerRoute.LIVE_RELAY)}"
        return self.forward(
            HttpMethod.POST,
            path,
            request.body,
            request.header(Header.CONTENT_TYPE) or MediaType.JSON,
        )

    def forward(
        self,
        method: HttpMethod,
        target: str,
        body: bytes = b"",
        media_type: Optional[str] = None,
    ) -> Response:
        """
        The relay is where a publisher that does not answer becomes an answer, so this
        is where connection errors are caught.

        :param method: The request's method.
        :param target: The path and query of the request.
        :param body: The request's body, for a change.
        :param media_type: The body's media type, for a change.
        :return: The publisher's answer.
        :raises LivePublisherUnreachable: When no live publisher answers.
        """
        headers = {} if media_type is None else {Header.CONTENT_TYPE: media_type}
        connection = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            with closing(connection):
                connection.request(method, target, body or None, headers)
                answer = connection.getresponse()
                body = answer.read()
        except (OSError, HTTPException) as error:
            raise LivePublisherUnreachable(
                self.address(), str(error) or type(error).__name__
            ) from error
        media_type = MEDIA_TYPES.get(answer.getheader(Header.CONTENT_TYPE, ""))
        if media_type is None:
            raise LivePublisherUnreachable(
                self.address(), "the service answering there is no live publisher"
            )
        return Response(
            status=HTTPStatus(answer.status), body=body, media_type=media_type
        )

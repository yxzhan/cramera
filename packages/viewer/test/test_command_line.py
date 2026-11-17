"""
The cramera command's configuration and screenshot command.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from cramera import screenshot
from cramera.command_line import DEFAULT_PORT, Option, ViewerConfiguration
from cramera.http import server as server_module
from cramera.http.addresses import EVERY_ADDRESS
from cramera.http.origin import KEY_PARAMETER, OriginPolicy
from cramera.http.server import HttpServer
from cramera.network_key import NetworkKey
from cramera.http.messages import HttpMethod
from cramera.scenes import SceneSource
from cramera.screenshot import (
    BrowserKind,
    PageReport,
    Screenshot,
    WebDriver,
    WebDriverKey,
)
from cramera_protocol.events import PageReportKey, PageState
from cramera_protocol.viewer import EnvironmentVariable, ViewerRoute

from .conftest import DATASET
from .dataset.webdriver import PNG_SIGNATURE, MimicWebDriver


def test_the_configuration_defaults_to_a_local_viewer(locations):
    configuration = ViewerConfiguration.from_arguments([], locations)
    assert (configuration.host, configuration.port, configuration.open_browser) == (
        "127.0.0.1",
        DEFAULT_PORT,
        True,
    )


def test_the_configuration_reads_every_option(locations, tmp_path):
    configuration = ViewerConfiguration.from_arguments(
        [
            Option.PORT,
            "9000",
            Option.SCENES,
            str(tmp_path / "a"),
            Option.DEMO_SCENES,
            str(tmp_path / "d"),
            Option.NO_BROWSER,
        ],
        locations,
    )
    sources = [directory.source for directory in configuration.scene_directories()]
    assert sources == [
        SceneSource.LOCAL,
        SceneSource.SHARED,
        SceneSource.SHARED,
        SceneSource.DEMO,
    ]
    assert configuration.port == 9000 and not configuration.open_browser


def test_the_configuration_reads_the_deployments_build_from_the_environment(
    locations,
):
    configuration = ViewerConfiguration.from_arguments(
        [], locations, {EnvironmentVariable.DEPLOYMENT: "03a7259-20261112-1830"}
    )

    assert configuration.deployment == "03a7259-20261112-1830"
    assert ViewerConfiguration.from_arguments([], locations).deployment is None


def test_the_lan_option_listens_on_every_address(locations):
    configuration = ViewerConfiguration.from_arguments([Option.LAN], locations)
    assert configuration.host == EVERY_ADDRESS


def test_the_lan_option_and_a_host_exclude_each_other(locations):
    with pytest.raises(SystemExit):
        ViewerConfiguration.from_arguments(
            [Option.LAN, Option.HOST, "127.0.0.1"], locations
        )


PUBLIC_ORIGIN = "https://cramera.example.org"
"""
The origin a password-checking proxy serves the viewer at in the tests.
"""


def test_a_public_origin_serves_the_viewer_to_a_proxy_on_this_machine(locations):
    configuration = ViewerConfiguration.from_arguments(
        [Option.PUBLIC_ORIGIN, PUBLIC_ORIGIN, Option.PORT, "8724"], locations
    )
    policy = configuration.policy()
    assert configuration.host == "127.0.0.1"
    assert policy.proxied and policy.key is None
    assert "cramera.example.org" in policy.allowed_hosts


def test_a_viewer_without_a_public_origin_keeps_its_policy(locations):
    assert ViewerConfiguration(locations=locations).policy() == OriginPolicy()
    network = ViewerConfiguration(locations=locations, host=EVERY_ADDRESS).policy()
    assert network.key and not network.proxied


@pytest.mark.parametrize(
    "arguments",
    [
        [Option.PUBLIC_ORIGIN, PUBLIC_ORIGIN, Option.LAN],
        [Option.PUBLIC_ORIGIN, PUBLIC_ORIGIN, Option.HOST, EVERY_ADDRESS],
        [Option.PUBLIC_ORIGIN, "cramera.example.org"],
        [Option.PUBLIC_ORIGIN, "ftp://cramera.example.org"],
        [Option.PUBLIC_ORIGIN, f"{PUBLIC_ORIGIN}/phone/"],
    ],
)
def test_a_public_origin_is_an_origin_a_proxy_on_this_machine_serves(
    locations, arguments
):
    with pytest.raises(SystemExit):
        ViewerConfiguration.from_arguments(arguments, locations)


def test_a_viewer_behind_a_proxy_names_its_public_origin(locations, answerer, caplog):
    configuration = ViewerConfiguration(
        locations=locations, public_origin=PUBLIC_ORIGIN
    )
    caplog.set_level(logging.INFO)
    with HttpServer(answerer=answerer) as server:
        configuration.announce_network(server)
    assert PUBLIC_ORIGIN in caplog.text


def test_a_viewer_on_the_network_names_its_network_address(
    locations, answerer, monkeypatch, caplog
):
    monkeypatch.setattr(server_module, "network_address", lambda: "192.0.2.7")
    configuration = ViewerConfiguration(locations=locations, host=EVERY_ADDRESS)
    caplog.set_level(logging.INFO)
    with HttpServer(answerer=answerer, host=EVERY_ADDRESS) as server:
        configuration.announce_network(server)
        key = NetworkKey.of(locations).value()
        address = f"http://192.0.2.7:{server.bound_port()}/?{KEY_PARAMETER}={key};"
        assert f"open {address}" in caplog.text


def test_a_viewer_on_every_address_without_a_network_says_so(
    locations, answerer, monkeypatch, caplog
):
    monkeypatch.setattr(server_module, "network_address", lambda: None)
    configuration = ViewerConfiguration(locations=locations, host=EVERY_ADDRESS)
    with HttpServer(answerer=answerer, host=EVERY_ADDRESS) as server:
        configuration.announce_network(server)
    assert "no network address" in caplog.text


def test_a_viewer_on_this_machine_only_names_no_network_address(
    locations, answerer, caplog
):
    configuration = ViewerConfiguration(locations=locations)
    caplog.set_level(logging.INFO)
    with HttpServer(answerer=answerer) as server:
        configuration.announce_network(server)
    assert caplog.text == ""


def test_a_viewer_on_the_network_keeps_its_key_from_one_start_to_the_next(locations):
    configuration = ViewerConfiguration(locations=locations, host=EVERY_ADDRESS)
    key = configuration.network_key()
    assert key and configuration.network_key() == key
    assert NetworkKey.of(locations).path.stat().st_mode & 0o777 == 0o600
    assert ViewerConfiguration(locations=locations).network_key() is None


def test_an_empty_key_file_gets_a_new_key(locations):
    key = NetworkKey.of(locations)
    key.path.parent.mkdir(parents=True, exist_ok=True)
    key.path.write_text("\n")
    assert key.value() and key.value() == key.path.read_text()


def test_the_configured_server_serves_the_viewer(locations):
    configuration = ViewerConfiguration(locations=locations, port=0, open_browser=False)
    with configuration.server() as server:
        assert server.bound_port() > 0


# %% screenshots


def test_browser_capabilities_ask_for_a_headless_window_of_the_given_size():
    for kind in BrowserKind:
        capabilities = kind.capabilities(800, 600)
        assert capabilities[WebDriverKey.BROWSER_NAME] == kind.value
        assert any(
            "800" in argument
            for options in capabilities.values()
            if isinstance(options, dict)
            for argument in options[WebDriverKey.ARGUMENTS]
        )


def test_a_screenshot_waits_until_the_page_is_ready(tmp_path):
    mimic = MimicWebDriver(states=[None, PageState.LOADING, PageState.READY])
    driver = WebDriver(BrowserKind.CHROME, port=mimic.start())
    output = tmp_path / "shots" / "viewer.png"
    report = Screenshot(url="http://localhost/", output=output, settle=0).take(driver)
    mimic.stop()
    assert report == PageReport(state=PageState.READY, status=mimic.status)
    assert report.succeeded()
    assert output.read_bytes() == PNG_SIGNATURE
    assert mimic.calls()[f"{HttpMethod.POST} /session/mimic/execute/sync"] == 3


def test_a_screenshot_of_a_failed_page_reports_the_failure(tmp_path):
    mimic = MimicWebDriver(states=[PageState.FAILED], status="Scene failed")
    report = Screenshot(
        url="http://localhost/", output=tmp_path / "s.png", settle=0
    ).take(WebDriver(BrowserKind.FIREFOX, port=mimic.start()))
    mimic.stop()
    assert not report.succeeded()


def test_a_page_that_never_reports_runs_into_the_timeout(tmp_path):
    mimic = MimicWebDriver(states=[PageState.LOADING])
    report = Screenshot(
        url="http://localhost/", output=tmp_path / "s.png", timeout=0.6, settle=0
    ).take(WebDriver(BrowserKind.CHROME, port=mimic.start()))
    mimic.stop()
    assert report.state == PageState.LOADING


def test_the_command_writes_the_report_and_exits_by_the_page_state(
    tmp_path, monkeypatch
):
    mimic = MimicWebDriver(states=[PageState.READY])
    port = mimic.start()
    monkeypatch.setattr(
        WebDriver, "start", lambda self: setattr(self, "port", port) or self
    )
    monkeypatch.setattr(WebDriver, "stop", lambda self: None)
    monkeypatch.setattr(Screenshot, "settle", 0)
    report_file = tmp_path / "report.json"
    code = screenshot.main(
        ["http://localhost/", str(tmp_path / "s.png"), "--report", str(report_file)]
    )
    mimic.stop()
    assert code == 0
    report = json.loads(report_file.read_text())
    assert report[PageReportKey.STATE] == PageState.READY
    assert report[PageReportKey.BROWSER] == BrowserKind.CHROME


def test_a_missing_driver_program_is_reported(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError):
        WebDriver(BrowserKind.CHROME).start()


# %% processes


def test_a_driver_program_is_started_and_stopped(tmp_path, monkeypatch):
    program = tmp_path / BrowserKind.CHROME.driver_program()
    program.write_text(
        f"#!{sys.executable}\n"
        + (DATASET / "listening_driver.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    with WebDriver(BrowserKind.CHROME) as driver:
        assert driver.is_listening()
        process = driver.process
    assert process.returncode is not None
    assert driver.process is None
    driver.stop()


def test_a_driver_that_never_listens_times_out(tmp_path):
    driver = WebDriver(BrowserKind.CHROME, port=WebDriver.free_port())
    with pytest.raises(TimeoutError):
        driver.wait_until_ready(timeout=0.3)


def started_viewer(tmp_path) -> tuple[subprocess.Popen, str]:
    """
    :return: A viewer command serving on a free port, and its address.
    """
    environment = {**os.environ, EnvironmentVariable.DATA: str(tmp_path)}
    environment.pop("PYTHONPATH", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cramera.command_line",
            Option.PORT,
            "0",
            Option.NO_BROWSER,
        ],
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    # Plugins may warn while they load, before the viewer announces its address.
    announced = next(
        line for line in iter(process.stderr.readline, "") if "cramera serves" in line
    )
    return process, announced.strip().rsplit(" ", 1)[-1]


def blocked_signals(pid: int) -> list[set[signal.Signals]]:
    """
    :return: The signals each thread of the process `pid` blocks; a thread it starts
        and a process that thread starts inherit them.
    """
    masks = [
        int(line.split()[1], 16)
        for task in Path(f"/proc/{pid}/task").iterdir()
        for line in (task / "status").read_text().splitlines()
        if line.startswith("SigBlk:")
    ]
    return [
        {number for number in signal.Signals if mask & (1 << (number - 1))}
        for mask in masks
    ]


def test_the_viewer_command_serves_until_terminated(tmp_path):
    process, url = started_viewer(tmp_path)
    with urllib.request.urlopen(url + ViewerRoute.INFO) as answer:
        assert answer.status == HTTPStatus.OK
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=20) == 0


def test_the_viewer_command_blocks_no_signal_its_children_would_inherit(tmp_path):
    """
    A process the viewer starts, such as a run on the real robot, inherits the signals
    blocked in the thread starting it; a blocked SIGTERM made a run ignore its Stop.
    """
    process, _ = started_viewer(tmp_path)
    try:
        # a library's helper thread blocking every signal starts no process
        assert [
            blocked
            for blocked in blocked_signals(process.pid)
            if signal.SIGTERM in blocked and signal.SIGUSR1 not in blocked
        ] == []
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
    assert process.returncode == 0

"""
Install cramera with the CRAM workspace into one virtual environment, so the whole stack
works: the viewer, its plugins, cramera-bridge and every package of the CRAM workspace,
all editable.

``python3 scripts/install_stack.py`` clones the CRAM workspace next to this repository
when it is not there yet, creates the environment with uv and installs everything in one
resolution. ``--viewer-only`` installs the viewer and its plugins without CRAM. The
script needs only the standard library and uv.

A package of the CRAM workspace whose distribution this repository also provides, such
as CRAM's own earlier ``cramera``, is left out, so this repository's package is the one
installed. cramera-bridge is installed from this repository while it lives here, and
from the CRAM workspace once it moved there.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import List, Optional, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
"""
This repository.
"""

CRAM_URL = "https://github.com/cram2/cognitive_robot_abstract_machine.git"
"""
Where the CRAM workspace is cloned from.
"""

CRAM_DIRECTORY_NAME = "cognitive_robot_abstract_machine"
"""
The name of the CRAM workspace's checkout.
"""


def main_checkout() -> Path:
    """
    :return: The repository's main checkout, also when this script runs from one of its
        git worktrees, or this repository outside of git.
    """
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if common.returncode != 0:
        return REPOSITORY_ROOT
    return Path(common.stdout.strip()).parent


DEFAULT_CRAM_DIRECTORY = main_checkout().parent / CRAM_DIRECTORY_NAME
"""
Where the CRAM workspace is looked for, and cloned to when it is missing: next to the
repository's main checkout.
"""

DEFAULT_ENVIRONMENT = Path.home() / ".virtualenvs" / "cramera"
"""
The virtual environment created when none is named.
"""

PYTHON_VERSION = "3.12"
"""
The Python version CRAM requires.
"""

DEVELOPMENT_REQUIREMENTS = REPOSITORY_ROOT / "requirements-development.txt"
"""
The formatters and test tools CI pins.
"""

ROS_PYTEST = "pytest>=7,<8"
"""
The pytest a stack with CRAM gets: ROS 2's ``launch_testing`` plugin, which the
environment sees through the system's site packages, does not load into pytest 8.
"""

PARALLEL_TESTS = "pytest-xdist"
"""
Runs a suite's tests in parallel, as ``-n 8`` asks.
"""

RENAMED_DISTRIBUTIONS = ("cramera-cram",)
"""
Earlier names of this repository's distributions; an environment that still holds one
registers coraplex's ``cramera`` visualization twice, and coraplex then loads neither.
"""

SUBMODULES_FILE = ".gitmodules"
"""
The file naming this repository's submodules, such as its object libraries.
"""

GIT_DIRECTORY = ".git"
"""
The entry that makes this repository a git checkout: a directory, or a file pointing
into a superproject's git directory when it is a submodule itself. A copy without it,
such as the one a container image is built from, already holds its submodules' files.
"""

PYPROJECT = "pyproject.toml"
"""
The file naming a package and, for the CRAM workspace, its members.
"""


# %% packages


class Extra(StrEnum):
    """
    The optional dependencies of this repository's packages the stack installs.
    """

    BRIDGE = "bridge"
    """
    The cramera-bridge package, through which a plugin reaches the CRAM workspace.
    """

    TEST = "test"
    """
    What the USD converter's tests need.
    """


@dataclass(frozen=True)
class PackageDirectory:
    """
    One package of this repository, relative to its root.
    """

    path: str
    """
    The package's directory.
    """

    extra: Optional[Extra] = None
    """
    The optional dependencies installed with it in a stack with CRAM.
    """

    needs_cram: bool = False
    """
    Whether the package is installed only together with CRAM.
    """

    def directory(self) -> Path:
        """
        :return: The package's absolute directory.
        """
        return REPOSITORY_ROOT / self.path

    def requirement(self, with_cram: bool) -> str:
        """
        :param with_cram: Whether the stack installs CRAM.
        :return: The editable requirement, with its extra where CRAM is installed.
        """
        if with_cram and self.extra is Extra.BRIDGE:
            return f"{self.directory()}[{self.extra}]"
        return str(self.directory())


VIEWER_PACKAGES = (
    PackageDirectory("packages/protocol"),
    PackageDirectory("packages/viewer"),
    PackageDirectory("packages/questions", extra=Extra.BRIDGE),
    PackageDirectory("packages/plugins/offline"),
    PackageDirectory("packages/plugins/laboratory", extra=Extra.BRIDGE),
    PackageDirectory("packages/plugins/scene-builder"),
    PackageDirectory("packages/plugins/plan-builder", extra=Extra.BRIDGE),
    PackageDirectory("packages/plugins/scene-runs"),
)
"""
The viewer and its plugins, which install without CRAM.
"""

CRAM_PACKAGES = (PackageDirectory("packages/bridge", needs_cram=True),)
"""
This repository's packages that need the CRAM workspace.
"""

USD_PACKAGE = PackageDirectory("packages/usd", extra=Extra.TEST)
"""
The USD converter, installed on request; it needs usd-core.
"""


def distribution_name(directory: Path) -> str:
    """
    :param directory: A package directory.
    :return: The distribution name its ``pyproject.toml`` declares, normalized.
    """
    with (directory / PYPROJECT).open("rb") as file:
        name = tomllib.load(file)["project"]["name"]
    return name.lower().replace("_", "-")


# %% CRAM workspace


@dataclass(frozen=True)
class CramWorkspace:
    """
    A checkout of the CRAM workspace.
    """

    directory: Path
    """
    The checkout's root.
    """

    def members(self) -> List[Path]:
        """
        :return: The directories of the workspace's packages, as its uv workspace lists
            them.
        """
        with (self.directory / PYPROJECT).open("rb") as file:
            names = tomllib.load(file)["tool"]["uv"]["workspace"]["members"]
        return [self.directory / name for name in names]

    def installable_members(self, provided: Sequence[str]) -> List[Path]:
        """
        :param provided: The distributions this repository installs itself.
        :return: The members whose distribution this repository does not provide.
        """
        return [
            member
            for member in self.members()
            if distribution_name(member) not in provided
        ]

    def clone_command(self) -> List[str]:
        """
        :return: The command cloning the workspace with its submodules.
        """
        return ["git", "clone", "--recurse-submodules", CRAM_URL, str(self.directory)]


# %% installation


@dataclass
class StackInstallation:
    """
    Installs the stack into one virtual environment.
    """

    environment: Path
    """
    The virtual environment.
    """

    cram: Optional[CramWorkspace]
    """
    The CRAM workspace, or None for the viewer alone.
    """

    with_usd: bool = False
    """
    Whether the USD converter is installed as well.
    """

    with_development_tools: bool = False
    """
    Whether the formatters and test tools are installed as well.
    """

    dry_run: bool = False
    """
    Whether the commands are only printed.
    """

    uv: str = field(default_factory=lambda: shutil.which("uv") or "")
    """
    The uv executable.
    """

    def python(self) -> Path:
        """
        :return: The environment's interpreter.
        """
        return self.environment / "bin" / "python"

    def repository_packages(self) -> List[PackageDirectory]:
        """
        :return: This repository's packages the stack installs; cramera-bridge only while
            it lives in this repository.
        """
        packages = list(VIEWER_PACKAGES)
        if self.cram is not None:
            packages += [
                package for package in CRAM_PACKAGES if package.directory().is_dir()
            ]
        if self.with_usd:
            packages.append(USD_PACKAGE)
        return packages

    def requirements(self) -> List[str]:
        """
        :return: The arguments of one ``uv pip install`` call installing everything
            editable.
        """
        packages = self.repository_packages()
        editable = [package.requirement(self.cram is not None) for package in packages]
        if self.cram is not None:
            provided = [
                distribution_name(package.directory())
                for package in VIEWER_PACKAGES + CRAM_PACKAGES + (USD_PACKAGE,)
                if package.directory().is_dir()
            ]
            editable += [
                str(member) for member in self.cram.installable_members(provided)
            ]
        arguments: List[str] = []
        for requirement in editable:
            arguments += ["-e", requirement]
        if self.with_usd:
            arguments.append("usd-core")
        if self.with_development_tools:
            arguments += self.development_tools()
        return arguments

    def development_tools(self) -> List[str]:
        """
        :return: The pinned formatters and test tools, with a pytest ROS 2 accepts in a
            stack with CRAM, and the parallel test runner.
        """
        lines = DEVELOPMENT_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        tools = [
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if self.cram is not None:
            tools = [
                ROS_PYTEST if tool.split("==")[0] == "pytest" else tool
                for tool in tools
            ]
        return tools + [PARALLEL_TESTS]

    def run(self, command: List[str]) -> None:
        """
        :param command: A command; printed, and run unless this is a dry run.
        :raises subprocess.CalledProcessError: When it fails.
        """
        print("+", " ".join(command), flush=True)
        if not self.dry_run:
            subprocess.run(command, check=True)

    def install(self) -> None:
        """
        Clone the CRAM workspace and fetch this repository's object libraries if needed,
        create the environment if needed, install everything and check that it imports.
        """
        if self.cram is not None and not self.cram.directory.is_dir():
            self.run(self.cram.clone_command())
        if (
            self.cram is not None
            and (REPOSITORY_ROOT / SUBMODULES_FILE).is_file()
            and (REPOSITORY_ROOT / GIT_DIRECTORY).exists()
        ):
            # the object libraries under resources/ the plan builder offers
            self.run(
                ["git", "-C", str(REPOSITORY_ROOT), "submodule", "update", "--init"]
            )
        if not self.python().exists():
            # ROS's Python packages, such as rclpy, live in the system's site packages
            self.run(
                [
                    self.uv,
                    "venv",
                    "--seed",
                    "--python",
                    PYTHON_VERSION,
                    "--system-site-packages",
                    str(self.environment),
                ]
            )
        # uv only warns about a distribution that is not installed
        self.run(
            [self.uv, "pip", "uninstall", "--python", str(self.python())]
            + list(RENAMED_DISTRIBUTIONS)
        )
        self.run(
            [self.uv, "pip", "install", "--python", str(self.python())]
            + self.requirements()
        )
        self.run([str(self.python()), "-c", self.import_check()])

    def import_check(self) -> str:
        """
        :return: Python code importing a module of every part of the stack.
        """
        modules = ["cramera", "cramera_protocol", "cramera_questions.plugin"]
        if self.cram is not None:
            modules += ["coraplex", "krrood", "cramera_bridge.questions.scenes"]
        return "; ".join(f"import {module}" for module in modules) + "; print('ok')"

    def next_steps(self) -> str:
        """
        :return: What to run once the stack is installed.
        """
        lines = [
            "",
            "Installed. Activate the environment and start the viewer:",
            f"  source {self.environment}/bin/activate",
            f"  cramera --demo-scenes {REPOSITORY_ROOT / 'demo'}",
            "",
            "The Medical lab tab can show its scene without MuJoCo. For physics,",
            "activate this environment, install the physics extra and restart cramera:",
            "  python -m ensurepip --upgrade  # only if this environment has no pip",
            f"  python -m pip install -e '{REPOSITORY_ROOT / 'packages/plugins/laboratory'}[physics]'",
        ]
        if self.cram is not None:
            lines += [
                "",
                "Robot demos and cramera-bridge's tests also need ROS 2 and the robot",
                "descriptions; source them before running one:",
                "  source /opt/ros/jazzy/setup.bash",
                "  source <your ROS workspace>/install/setup.bash",
                f"See 'Setup your ROS Workspace' in {self.cram.directory / 'README.md'}.",
            ]
        return "\n".join(lines)


# %% command line


def main(arguments: Sequence[str]) -> int:
    """
    :param arguments: The command line arguments.
    :return: The exit status.
    """
    parser = argparse.ArgumentParser(
        description=" ".join(__doc__.strip().split("\n\n")[0].split())
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_ENVIRONMENT,
        help=f"the virtual environment to install into (default {DEFAULT_ENVIRONMENT})",
    )
    parser.add_argument(
        "--cram",
        type=Path,
        default=DEFAULT_CRAM_DIRECTORY,
        help=f"the CRAM workspace checkout, cloned when missing "
        f"(default {DEFAULT_CRAM_DIRECTORY})",
    )
    parser.add_argument(
        "--viewer-only",
        action="store_true",
        help="install the viewer and its plugins without CRAM",
    )
    parser.add_argument(
        "--with-usd", action="store_true", help="also install the USD converter"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="also install the formatters and test tools",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands without running them"
    )
    options = parser.parse_args(arguments)
    installation = StackInstallation(
        environment=options.venv.expanduser().absolute(),
        cram=(
            None
            if options.viewer_only
            else CramWorkspace(options.cram.expanduser().absolute())
        ),
        with_usd=options.with_usd,
        with_development_tools=options.dev,
        dry_run=options.dry_run,
    )
    if not installation.uv:
        print(
            "uv is needed; install it with\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            "and run this script again.",
            file=sys.stderr,
        )
        return 1
    installation.install()
    print(installation.next_steps())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

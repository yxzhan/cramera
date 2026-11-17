#!/bin/bash
# Builds the ROS packages ros_overlay.tsv names into the overlay directory $1: each
# repository is fetched at its commit alone, and only the named packages are built.
set -euo pipefail

overlay="$1"
packages_file="$(dirname "$0")/ros_overlay.tsv"
checkouts="$(mktemp -d)"
mkdir -p "${overlay}/src"

grep -v '^#' "${packages_file}" | while IFS=$'\t' read -r repository commit packages; do
    checkout="${checkouts}/$(basename "${repository}" .git)"
    git init -q "${checkout}"
    git -C "${checkout}" fetch -q --depth 1 "${repository}" "${commit}"
    git -C "${checkout}" checkout -q FETCH_HEAD
    for package in ${packages}; do
        manifest="$(grep -rl --include=package.xml "<name>${package}</name>" "${checkout}" | head -n 1)"
        if [[ -z "${manifest}" ]]; then
            echo "${repository} at ${commit} has no package ${package}" >&2
            exit 1
        fi
        cp -r "$(dirname "${manifest}")" "${overlay}/src/${package}"
    done
done
rm -rf "${checkouts}"

cd "${overlay}"
set +u
source "/opt/ros/${ROS_DISTRO}/setup.bash"
set -u
colcon build --merge-install --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
rm -rf build log

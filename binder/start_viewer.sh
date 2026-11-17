#!/bin/bash
# Starts the cramera viewer on the port Jupyter's server proxy gives it ($1), with the
# demo scenes, the laboratory and the robot runs of runs.json, behind the proxy's origin.
set -eo pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /opt/cramera/ros-overlay/install/setup.bash

export CRAMERA_DATA="${CRAMERA_DATA:-${HOME}/.cramera}"
export CRAMERA_SCENE_RUNS="${CRAMERA_SCENE_RUNS:-/opt/cramera/cramera/binder/runs.json}"
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_LOG_DIR="${CRAMERA_DATA}/ros-log"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1

exec /opt/cramera/venv/bin/cramera \
    --host 127.0.0.1 --port "$1" --no-browser --keep-running \
    --demo-scenes /opt/cramera/cramera/demo \
    --scenes /opt/cramera/cramera/packages/plugins/laboratory/src/cramera_laboratory/resources \
    --public-origin "${CRAMERA_PUBLIC_ORIGIN:-https://jupyter.intel4coro.de}"

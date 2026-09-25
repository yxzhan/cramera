# Laboratory access from another computer

## Permanent server

The password-protected server address is
[https://cramera.informatik.uni-bremen.de/laboratory/](https://cramera.informatik.uni-bremen.de/laboratory/).
It runs its own MuJoCo laboratory on the university server and uses the existing
laboratory password. The workstation can be switched off after deployment;
its Cloudflare share is a separate simulation and remains available while that
workstation is running.

The existing application at the domain root remains unchanged. Nginx forwards
only `/laboratory/` to the authenticated laboratory gateway. General CRAMERA
execution endpoints are outside that gateway's allowlist. Both the gateway
and simulation backend listen only on loopback, run as `cramera-lab`, and
restart automatically through dedicated systemd services.

Code is deployed under `/srv/cramera-laboratory`, scene state under
`/var/lib/cramera-laboratory`, and private authentication files under
`/etc/cramera-laboratory`. The existing server virtual environment and package
dependencies are reused read-only through service-private mounts; installing
the laboratory does not alter that environment or the existing `cramera.service`.

To update the isolated deployment, set `CRAMERA_DEPLOY_IDENTITY` to the path of
your existing SSH identity, then stage and activate the selected files:

```bash
cd /home/hassouna/cram-worktrees/cram-viz
export CRAMERA_DEPLOY_IDENTITY=/path/to/your/ssh-identity
bash scripts/deploy_cramera_laboratory.sh stage
bash scripts/deploy_cramera_laboratory.sh activate
```

Staging copies the CRAMERA and Coraplex source, the three laboratory scene
bundles, and the PR2 collision description with its referenced meshes. It
does not copy the full repository or unrelated scenes. The password is read
from the existing local sharing state; `CRAMERA_DEPLOY_PASSWORD_FILE` can
select another private file. Activating restarts only the isolated laboratory,
checks both listeners, validates nginx, and enables the services for boot.
An activation resets that server's simulation and browser login sessions.

Inspect the server with `systemctl status cramera-laboratory-backend
cramera-laboratory-gateway` and `journalctl -u cramera-laboratory-backend
-u cramera-laboratory-gateway`. The reproducible service and nginx definitions
are in `scripts/laboratory-deployment/`.

## Workstation Cloudflare share

The laboratory runs on the workstation in this checkout:
`/home/hassouna/cram-worktrees/cram-viz`, branch `cramera-port`.
Its application code is in `cramera/src/cramera`, and browser code is in
`cramera/src/cramera/web`. Generated laboratory scenes are stored in
`~/.cramera/scenes/precision_lab*`.

The public share uses a password-protected laboratory gateway on
`127.0.0.1:8717`, followed by a Cloudflare Quick Tunnel providing HTTPS.
The original CRAMERA server remains on `127.0.0.1:8716`.
The gateway exposes the laboratory scene and its simulation controls.

## Start, inspect, and stop

Start the usual laboratory server if it is not already running:

```bash
cd /home/hassouna/cram-worktrees/cram-viz
CRAMERA_SCENE=precision_lab .venv/bin/python -m cramera.server 8716 --no-browser
```

In another terminal, start the share:

```bash
cd /home/hassouna/cram-worktrees/cram-viz
./scripts/share_cramera_lab.sh start
```

The command prints the HTTPS laboratory address and generated password.
Open that address on the other laptop and enter the password. The laptop
needs a browser with WebGL support; Python, ROS, and MuJoCo run on the
workstation. Both browsers control the same simulation session.

Read the current address and password again, or stop remote access:

```bash
./scripts/share_cramera_lab.sh status
./scripts/share_cramera_lab.sh stop
```

Stopping the share leaves the local CRAMERA server running. Starting an
already running share reuses its current address. If only the gateway has
stopped, `start` restores it while retaining the running tunnel and its current
address. A failed gateway recovery also leaves that tunnel available for a
subsequent retry. Log in again after the gateway restarts, because its browser
session token changes.

The workstation must remain
powered on and connected to the internet. A new tunnel receives a new address
after it is restarted; this is temporary access rather than a permanent domain.
See the official [Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
for the service's temporary tunnel behavior.

## Runtime files

The helper requires the project virtual environment, `curl`, `openssl`, `rg`,
and the official `cloudflared` binary at `~/.local/bin/cloudflared`.
`CRAMERA_CLOUDFLARED` can override the binary path.

Private process IDs, logs, the password, and the current public origin are
stored in `~/.local/state/cramera/share` (or `CRAMERA_SHARE_STATE` when set).
The directory is restricted to its owner, and the password is stored with
mode `0600`. These files are outside the served website and repository.
For startup failures, inspect `gateway.log` and `tunnel.log` in that directory.

An embedded browser previously failed to create a WebGL context on this
workstation. The remote laptop uses its own graphics driver and browser;
successful HTTP access alone does not verify that browser's 3D rendering.

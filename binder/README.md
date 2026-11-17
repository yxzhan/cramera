# cramera on Binder and Docker

This directory builds one image with the viewer, every plugin, CRAM at a pinned commit
and the ROS packages the robots and worlds come from, on IntEL4CoRo's Jupyter ROS 2
image. In the image, Jupyter's server proxy serves the viewer at `/cramera/`, and the
server opens that page by default.

## Open it on Binder

```
https://binder.intel4coro.de/v2/gh/AICOR-Solutions/cramera/binder?urlpath=cramera/%3Ffullscreen
```

The link builds this repository's `binder` branch, not `main`, so commits to `main`
leave the built image alone. Binder builds again only at the first launch after the
`binder` branch moved; this takes a while. To update the image, merge `main` into
`binder` and open the link once. Later launches start in about a minute. The page opens
straight into the viewer. The first click or key press puts it on the full screen,
as F11 does; Escape leaves the full screen. `urlpath=cramera/%3Fscene%3Dgarmi_pick_place%26fullscreen`
opens one scene; `urlpath=lab` opens JupyterLab instead.

What it shows:

- the recorded scenes of `demo/standard_scenes`, of the laboratory and of
  [`solutions-cramera-scenes`](https://github.com/AICOR-Solutions/solutions-cramera-scenes);
- **Physical lab**, the medical laboratory page;
- **Runs**: the robot runs of [`runs.json`](runs.json), started from the page,
  followed live and replayed afterwards: GARMI carrying a bowl and a spoon in the
  apartment, held at each of its queries until **Next**.

The PR2's laboratory runs replay as recordings only: `cramera_bridge.laboratory` still
imports `coraplex.datastructures.dataclasses`, which CRAM removed on 7 Oct 2026.

CRAM is pinned by `CRAM_REF` in the `Dockerfile` to the newest commit cramera's robot
demos run with; move it forward once `cramera-bridge` follows CRAM's main.

## Run it with Docker

```bash
docker build -f binder/Dockerfile -t cramera-binder .
docker run --rm -p 8888:8888 -e CRAMERA_PUBLIC_ORIGIN=http://localhost:8888 \
    cramera-binder start-notebook.py --IdentityProvider.token=''
```

and open http://localhost:8888/cramera/?fullscreen. Behind another address, set
`CRAMERA_PUBLIC_ORIGIN` to the origin people open, such as `https://cramera.example.org`;
without it the viewer accepts pages of `https://jupyter.intel4coro.de`, where
binder.intel4coro.de runs its servers, and of this machine only.

## Files

| File | What it does |
| --- | --- |
| `Dockerfile` | The image; `CRAM_REF` and `SCENES_REF` choose other commits or branches. |
| `ros_overlay.tsv`, `build_ros_overlay.sh` | The ROS packages of the robots and worlds, at the commits the CRAMERA phone server runs them with. |
| `start_viewer.sh` | Starts the viewer on the port the server proxy gives it. |
| `jupyter_server_proxy.json` | Registers the viewer with the server proxy and makes it the default page. |
| `runs.json` | The robot runs the Runs tab offers. |

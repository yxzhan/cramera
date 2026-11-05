# cramera

**Watch a CRAM robot run, recorded or live, in the browser, and ask the scene what happened.**

**[Open the demo](https://aicor-solutions.github.io/cramera/)** · [What is published right now](https://aicor-solutions.github.io/cramera/status.html) · [Architecture](docs/architecture.md) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/AICOR-Solutions/cramera/issues)

[![The viewer as the latest build shows it](https://aicor-solutions.github.io/cramera/status/viewer.png)](https://aicor-solutions.github.io/cramera/)

The picture above is taken by every build of `main`: it always shows the newest
published viewer. The [status page](https://aicor-solutions.github.io/cramera/status.html)
names the version, the commit and the build time.

cramera is the viewer of the [CRAM](https://github.com/cram2/cognitive_robot_abstract_machine)
cognitive architecture. CRAM publishes what a robot does; cramera shows it:

- **Recorded.** A run becomes a self-contained scene bundle (URDFs, meshes and the
  recorded trajectory) that replays in any browser, without ROS, a simulator or
  Python. The demo site is exactly that: static files.
- **Live.** Attached to a running plan, the viewer follows the executing world.
- **Asked.** Questions in plain English or in CRAM's entity query language are
  answered over the recorded or running world.

## Version 1.0

Version 1.0 is a rewrite into independent packages that talk through one
versioned protocol, the way ROS and RViz do. The earlier single package is kept on
the branch [`archive/pre-v1`](https://github.com/AICOR-Solutions/cramera/tree/archive/pre-v1).

| Package | What it does | State |
| --- | --- | --- |
| [`cramera-protocol`](packages/protocol) | The formats and messages: scene bundles, the scenes index, live messages, routes, versions. Python and the browser read the same definitions. | done |
| [`cramera`](packages/viewer) | The viewer: HTTP server, scene catalog, plugins, the browser application, screenshots, the static site export. Runs without CRAM. | done |
| [`cramera-usd`](packages/usd) | Converting an Isaac Sim or USD stage into a scene bundle, with the export gate and the robot separation audit. | done |
| [`cramera-bridge`](packages/bridge) | Live publishing from a running plan, recording, onboarding demos, task demos. | done |
| [`cramera-questions`](packages/questions) | The viewer plugin asking recorded scenes questions in the entity query language; `cramera-bridge`'s question engine answers them. | done |
| [`cramera-scene-builder`](packages/plugins/scene-builder) | Compose scenes from bundles, meshes and reference scans in a full-window builder. | done |
| [`cramera-offline`](packages/plugins/offline) | Guided presentations without a robot or network, and `cramera-check-bundle`. | done |
| [`cramera-laboratory`](packages/plugins/laboratory) | A dedicated Physical lab page with the small workbench, real PR2 preview, local robot demo and replay, plus manual MuJoCo controls and setup help. | done |
| [`cramera-scene-runs`](packages/plugins/scene-runs) | A Runs tab that starts the robot runs a server offers, one at a time, follows each live and replays its recording; for a viewer behind a password-checking proxy. | done |
| [`cramera-shared-sim`](packages/plugins/shared-sim) | A Shared Sim tab showing Robot Lobby, a MuJoCo world in the browser shared by everyone connected, published on its own and embedded in a frame. | done |
| [`cramera-plan-builder`](packages/plugins/plan-builder) | Compose a plan from the CRAM actions it discovers, objects and plain-language constraints, write it as a CRAM demo, and run it live from the page. | done |

Recordings made with earlier versions open unchanged: a document without a
`formatVersion` is read as version 0.

## Install the whole stack

One command installs the viewer, every plugin, `cramera-bridge` and the CRAM workspace
into one virtual environment, all editable:

```bash
python3 scripts/install_stack.py
source ~/.virtualenvs/cramera/bin/activate
cramera --demo-scenes demo
```

It needs only Python 3 and [uv](https://github.com/astral-sh/uv) (`curl -LsSf
https://astral.sh/uv/install.sh | sh`). The CRAM workspace is taken from
`cognitive_robot_abstract_machine` next to this repository's checkout, or cloned there
with its submodules when it is missing; `--cram DIR` names another checkout and `--venv
DIR` another environment. The environment sees the system's site packages, so ROS 2's
`rclpy` works in it. `--viewer-only` installs the viewer and its plugins without CRAM,
`--with-usd` adds the USD converter, `--dev` the formatters and test tools (with pytest
below 8 next to CRAM, since ROS 2's `launch_testing` plugin does not load into pytest 8),
and `--dry-run` prints the commands without running them. Running it again updates the
environment in place.

It also fetches the object libraries the plan builder offers, the submodules under
`resources/` (`git submodule update --init`).

Robot demos and `cramera-bridge`'s tests also need ROS 2 Jazzy and the robot
descriptions: source `/opt/ros/jazzy/setup.bash` and the ROS workspace that CRAM's
README sets up ("Setup your ROS Workspace") before running one.

## Use the viewer

For the compact medical workbench and its PR2 demo, see
[Run the medical lab locally](docs/local-medical-lab.md). **Physical lab** in the
top bar opens its own page at `/plugins/laboratory/laboratory.html`, with a 3D view,
controls and playback timeline. With the full CRAM stack and ROS robot descriptions
installed, it opens the small lab with the real PR2. **Run the demo** follows the
locally executing transfer live, then opens its recording. The page also explains
how to add MuJoCo for the separate manual contact simulation.

```bash
pip install ./packages/protocol ./packages/viewer
cramera                      # serves http://127.0.0.1:8711 and opens it
```

The viewer shows the bundles of three scenes directories; a bundle in an earlier
one hides a bundle of the same name in a later one:

| Directory | Set by |
| --- | --- |
| local recordings | `$CRAMERA_DATA/scenes`, by default `~/.cramera/scenes` |
| shared scenes | `$CRAMERA_SCENES`, and every `--scenes DIR` |
| demo scenes | `--demo-scenes DIR`, for example `demo` of this repository |

```bash
cramera --demo-scenes demo --port 8711 --no-browser
```

A viewer started on a port another cramera viewer still holds stops that viewer and
takes the port over; pass `--keep-running` to fail instead.

A scenes directory may hold other scenes directories: every child that is not a
bundle but holds an `index.json` or bundles counts as a further directory of the same
source, and so on below it. So `demo` holds `demo/standard_scenes`, this repository's
recordings, which replay without CRAM: the PR2 working in the small medical lab,
`aicor_medical_lab_pr2_workflow`, and `aicor_medical_lab_pr2_query_stepper`, whose
Queries panel steps through the queries the PR2 asked, and GARMI picking up a bowl
and a spoon in an apartment, `garmi_pick_place`; and any scenes
repository cloned next to it, such as
`AICOR-Solutions/solutions-cramera-scenes`, is shown without being named. Clones under
`demo` other than `standard_scenes` are ignored by git.

```bash
git clone git@github.com:AICOR-Solutions/solutions-cramera-scenes.git demo/solutions-cramera-scenes
cramera --demo-scenes demo
```

The demo scenes are part of this repository, not of the installed package, so a
freshly installed viewer without `--demo-scenes` or `$CRAMERA_SCENES` shows "No
scenes found". A scenes directory that does not exist is skipped silently; give an
absolute path, or set `CRAMERA_SCENES`, when the viewer is not started from this
repository's root:

```bash
export CRAMERA_SCENES=/path/to/cramera/demo
cramera
```

### Run the wood workshop locally

See [Local workshop setup](docs/local-workshop.md) to copy the deployed scenes from
SSH into the latest viewer, or run the historical interactive MuJoCo workshop in a
separate environment.

### Install the pages and panels

Everything beyond the scene page is a plugin: Plan Builder, Scene Builder, Physical lab,
the questions column and the storyboard and bundle check routes of `cramera-offline`. Each is a pip package of its
own under `packages/plugins` (and `packages/questions`) that the viewer discovers through the entry-point group
`cramera.plugins`. `pip install ./packages/viewer` installs none of them, so the top
bar of a bare viewer shows no pages; an uninstalled plugin simply has no link. Install
the ones you want, or all of them:

```bash
pip install ./packages/questions ./packages/plugins/*
```

Restart `cramera` afterwards; its `/api/info` answer lists the plugins it found. Every
plugin installs with the viewer alone and declares what it needs for its full
behaviour as an optional extra:

| Plugin | Extra | Without it |
| --- | --- | --- |
| `cramera-plan-builder` | `[bridge]`, that is `cramera-bridge` inside the CRAM environment | The page opens, but its catalog names the CRAM modules it misses and no plan can run. |
| `cramera-laboratory` | `[physics]` installs MuJoCo for manual controls; `[bridge]` adds the bridge, which also needs the full CRAM workspace and ROS robot descriptions for the PR2 preview and demo. | The dedicated page opens the small workbench and explains the missing setup. Saved recordings remain viewable. |

```bash
pip install './packages/plugins/plan-builder[bridge]'
```

See [docs/plugins.md](docs/plugins.md) for writing a plugin.

The page has the look of the original AICOR viewer: dark, with the AICOR logo and a
link to every page the installed plugins offer (Plan Builder, Scene Builder, Physical lab) in the
top bar, the 3D view with its **Layers**
overlay and the playback controls on the left, questions and graphs on the right. The
**Dark | Light** switch in the top bar changes the theme. **Pop out** opens the scene
alone in a window of its own.

The page address names the shown bundle, `?scene=<name>`, so a view can be shared;
`?panel=<name>` opens a panel first.
The server listens on `127.0.0.1` and answers only pages served from the same
machine, unless `--lan` lets a phone watch.

### Watch from a phone

```bash
cramera --lan --demo-scenes demo
```

listens on every address of the machine and names the address other devices of its
network open, with a key:

```text
cramera serves http://127.0.0.1:8711
other devices of this network open http://192.168.1.23:8711/?key=…; they may watch, but change nothing
```

Open that address on the phone; it keeps the key in a cookie, so the page's own
requests and an address without the key work from then on. The key stays in
`~/.cramera/network_key` (`$CRAMERA_DATA`), so a bookmark outlives a restart; delete
the file for a new key. Other devices need the key for every request, and with it they
only read: replaying and switching scenes work, while asking questions, running plans
and saving are refused for them and stay with the machine that serves the viewer.

On a phone the columns stack, the scene picker wraps, a phone on its side fits the 3D
view and the playback controls onto its screen, and the 3D view takes one finger to
orbit, two to zoom and pan, and a tap to select. The demo site needs no `--lan`; it
opens on a phone as it is.

The phone and the machine must reach each other: a firewall has to let the port
through (`sudo ufw allow 8711/tcp` where ufw runs), and networks that keep their
devices apart, as many public and university networks do, need the phone's hotspot or
another network.

A running plan is followed from the phone as on the machine: open
`http://192.168.1.23:8711/?live=http://127.0.0.1:8765`, or connect in the **Live** tab
with its default address. The live publisher keeps answering this machine only, so a
page opened at the network address reads it through the viewer, below `/api/live/`:
the world, its states, the plan, the statecharts, the recording's status, the
resolutions, the query steps and the perception images of the publisher at
`127.0.0.1:8765`, and nothing that changes the run. Stopping and saving the recording
stay with the machine.

### Watch a remote server or container

Every page reads the live publisher through the viewer, so the viewer's port is the
only one to reach. `cramera connect` does that from a laptop through your own `ssh`:

```bash
cramera connect user@server                               # viewer on the server itself
cramera connect user@server --container my-cram --start   # start one inside a container
cramera connect user@server --container my-cram --local-port 18711
```

It opens the browser at `http://127.0.0.1:8711` (or `--local-port`) and closes the
connection on Ctrl-C. Keys and `~/.ssh/config` stay with `ssh`. Nothing on the server
listens beyond its loopback address: the viewer's port is forwarded, or, with
`--container`, each connection is carried by `docker exec -i <container> cramera pipe`
into the container, so the container needs no published ports. The page counts as the
server's own and keeps full control: stepping, stopping and saving. `--start` also
starts the viewer there and ends it with the connection; `--remote-command` says how
the server runs `cramera` when it is not on its `PATH`, such as
`--remote-command /opt/venv/bin/cramera`.

### Serve behind a proxy

A server reached over the internet serves the viewer through a reverse proxy on the
same machine that checks a password, such as nginx with Basic auth:

```bash
cramera --no-browser --port 8724 --public-origin https://cramera.example.org
```

```nginx
location = /phone { return 301 /phone/; }
location /phone/ {
    auth_basic "cramera";
    auth_basic_user_file /etc/cramera-phone/htpasswd;
    proxy_pass http://127.0.0.1:8724/;
    proxy_set_header Host $host;
    proxy_buffering off;
}
```

The viewer then listens on a loopback address only and counts every request as another
device's that the proxy let in, so none needs the key. Besides reading, such devices
make only the changes a route offers them: stepping through a live run's queries,
relayed below `/api/live/`, and starting and stopping the runs a plugin offers.
Asking questions, building and running plans and saving stay with the machine, and the
plan builder's folder listing is not offered over the network.

With [`cramera-scene-runs`](packages/plugins/scene-runs) installed and
`CRAMERA_SCENE_RUNS` naming a file of runs, the page gets a **Runs** tab: it starts one
of the offered runs at a time, such as a robot demo held at each of its queries, follows
it live, stops it and replays its recording.

### Scene graph and ontology

The **Scene graph** tab shows the shown world as a tree: the models with their links
nested by their joints, the free objects, the robot's parts and the world's other
semantic annotations, such as a drawer with its handle or a cabinet with its doors,
each referring to the links and objects it gives a meaning to. Choosing a node makes
what it stands for glow in the 3D view, a model all its links, an annotation all its
bodies and a group all its members, and **Focus** moves the camera onto it. Where the
scene names the classes of its entities, a node links each class, such as
`PR2LeftArm`, `Drawer`, `Body` or the joint's `RevoluteConnection`, to the
**Ontology** page, which opens cram_owlviz at that class in a tab of its own; every
further class link shows its class in the same tab.

Recordings name the classes of their entities and their world's semantic annotations.
Name them in bundles recorded before, in the CRAM environment with ROS and the robot
descriptions sourced, with

```bash
cramera-record-classes demo/standard_scenes            # every bundle of the directory
cramera-record-classes ~/.cramera/scenes --scene my_recording
```

### Commands

| Command | What it does |
| --- | --- |
| `cramera` | Serve the viewer. `--lan` serves the network too (see [Watch from a phone](#watch-from-a-phone)), `--public-origin ORIGIN` a password-checking proxy (see [Serve behind a proxy](#serve-behind-a-proxy)). `--logo FILE` (or `CRAMERA_LOGO`) shows another `.png`, `.svg` or `.jpg` instead of the AICOR logo in every page's top bar, `--logo-text TEXT` says what it shows. |
| `cramera connect USER@SERVER` | Open a viewer that runs on another machine, or with `--container NAME` in a container there, in this machine's browser through `ssh` (see [Watch a remote server or container](#watch-a-remote-server-or-container)). |
| `cramera-shot URL OUT.png` | Take a screenshot in headless Chrome (`--browser chrome`, needs `chromedriver`) or Firefox (`--browser firefox`, needs `geckodriver`) once the page reports its scene as shown; exits 1 when it fails. |
| `cramera-export-site OUT --scenes DIR [--plugin NAME] [--logo FILE]` | Write the viewer and the bundles of a directory as a static site, for any web server, with the browser files of the named installed plugins and the given logo. |
| `cramera-upgrade-bundle SRC DST` | Copy a bundle and rewrite its documents in the current format, optionally with new labels for the scene picker. |

## Follow a running plan

`cramera-bridge` publishes a running CRAM world on `http://127.0.0.1:8765`. Run a demo
file, unchanged, with every plan executor it creates for its world shown in the viewer,
and keep publishing until Ctrl-C:

```bash
pip install ./packages/questions ./packages/bridge   # inside the CRAM environment
cramera-live demo.py -- --demo-option value
cramera --no-browser
# then open http://127.0.0.1:8711/?live=http://127.0.0.1:8765
```

The viewer starts without a recorded scene, loads the live world, and follows its
joint states, its plan and the statechart the plan runs as. The **Live** tab connects to another
publisher, stops and saves the recording as a bundle, and replays it.
`cramera-onboard demo.py --name NAME` runs a demo and saves its run as a bundle
without keeping a publisher up.

A robot task demo started with `--step-queries` is held at each query it asks of its
own until **Next** in the **Queries** panel moves it on, and saves its recording with
the queries (`--name` names it, `--no-hold` runs on). The PR2's vessel transfer and
GARMI carrying a bowl and a spoon across the apartment ship as such demos:

```bash
python -m cramera_bridge.demos.query_stepping --bundle ~/.cramera/scenes/aicor_medical_lab
python -m cramera_bridge.demos.garmi_apartment --step-queries
```

### Segment a run with segmind

With `cramera-onboard demo.py --name NAME --detect-events`, or `CRAMERA_DETECT_EVENTS=1`
for a demo that publishes live, segmind watches the run's objects with its detectors of
every kind, as it watches a run of its own: contacts, motions, support, containment,
grasps, pick-ups and placings. The recording lists every event on the frame it happened
at, and the **Segmind** tab shows them in order, live as they are detected and in the
replay: choosing one lights its objects up and jumps to its moment, the event the
playback passed last is marked (**Light up events during playback** lights up its
objects as well), the sorts of event can be switched on and off, and the timeline ticks
each event shown under the slider.

## Ask the scene

With `cramera-questions` installed, the viewer's right column gets:

- **EQL · entity query language**, which offers the presets of the shown scene and
  takes questions in plain English or in CRAM's entity query language. The question
  is read back in English; an answer is a table whose entities light up in the 3D
  view, and **Play NEEM** jumps to the moment an answer is about.
- **Plan**, **Statechart** and **Kinematics** tabs, which draw the scene's plan,
  statechart and robot kinematics as graphs. Clicking a node
  shows its details and lights it up in the 3D view; drag to pan, scroll or use the
  buttons to zoom, and fill the window with the graph.

Answering needs `cramera-bridge`'s question engine and krrood from the CRAM workspace
(`pip install -e 'packages/questions[bridge]'`). Where they are missing, and on the
static demo site, the plugin shows the answers recorded in the bundle instead: the
presets answer as they did when they were recorded, the graphs are drawn, and the
query boxes say why they are disabled. Record them in the CRAM environment with

```bash
cramera-record-answers demo/standard_scenes            # every bundle of the directory
cramera-record-answers packages/plugins/laboratory/src/cramera_laboratory/resources --scene aicor_medical_lab
```

which writes `recorded_answers.json` into each bundle; record again after a bundle
or the presets change.

## Import a USD stage

`cramera-import` turns a USD stage (`.usd`, `.usda`, `.usdc`, for example an Isaac Sim
twin) into a self-contained scene bundle of its environment, which the viewer shows;
the robots that act in it are added by CRAM, not by the import. It needs
`cramera-usd` (usd-core, numpy, scipy, Pillow):

```bash
pip install ./packages/protocol ./packages/usd
cramera-import path/to/lab.usda --name my_lab \
  --environment-name "My lab" --task "Cell 01, static" \
  --camera-position 8.7 -5.9 2.95 --camera-target -2.6 0.9 0.7 --camera-fov 61.6
```

The bundle lands in `<scenes directory>/my_lab/` and the `index.json` next to it is
rebuilt from the bundles on disk. Without `--out` the scenes directory is the one the
viewer reads: `$CRAMERA_SCENES`, else `$CRAMERA_DATA/scenes`, else
`~/.cramera/scenes`.

| Option | Meaning |
| --- | --- |
| `--name NAME` | bundle name: letters, digits, `_` and `-` (required) |
| `--out DIR` | scenes directory to write into |
| `--prim PATH` | convert only this prim's subtree; repeatable |
| `--exclude PRIM` | leave out this prim's subtree; repeatable |
| `--keep-robots` | keep the robots the stage marks in the environment (left out by default) |
| `--free-objects` | write the standalone rigid bodies of a static scene as free objects a CRAM world can pick up (see below) |
| `--environment-name`, `--task` | the scene's labels in the viewer's picker |
| `--exposure` | tone-mapping exposure (default 0.72) |
| `--camera-position X Y Z`, `--camera-target X Y Z` | initial view, given together; `--camera-fov DEGREES` (vertical) only with both. Without them the stage's first camera is used, else a view framing the bulk of the geometry |
| `--variant PRIM SET VARIANT` | select a variant in memory (the file is not changed); repeatable |
| `--trace FILE.npz` | play back a recorded body trace (`bodies`, `pos`, `quat` w-first, `sample_hz`) instead of the stage's time samples |
| `--fps` | playback rate (default 30) |
| `--robot PRIM` | the robot's root link; the robot becomes a model of its own with a base pose track, kept although the stage marks it |
| `--record FILE.json` | a run record whose steps become the timeline segments |

**Robots stay out by default.** A prim the stage marks as a robot is left out with
everything below it: Isaac Sim's robot schema (`IsaacRobotAPI`) in the prim's authored
API schemas, or an `sdt:entityType` of exactly `Robot`. A `--prim` that is a robot, or
lies inside one or inside an `--exclude`, is refused. Joints from the environment to a
robot's bodies are dropped, so whatever a robot held stays where the stage puts it,
fixed to the world. `conversion_report.json` lists every prim left out and why
(`excluded`), and two more lists point at robot parts the marks miss:

- `detached`: every converted body that hung from a left-out body, with the joint that
  held it and the movable joints below it. A held object has none; a body that still
  moves parts, such as a gripper beside the robot's marked prim, needs `--exclude`.
- `articulations`: every articulation root with movable joints converted outside the
  marked robots. Doors and stations are articulations too; a robot the stage does not
  mark is one of them and needs `--exclude`.

A recorded run is a replay rather than an environment: `--robot` keeps the robot it
replays, and `--keep-robots` keeps the other marked robots as well.

```bash
cramera-import demo.usda --name my_lab_run --variant /World/Robot Physics physics \
  --trace demo_trace.npz --robot /World/Robot --record demo_record.json
```

What carries over:

- **Geometry** in metres with Z up (other units and Y-up stages are converted),
  including instanced prims. Guide, proxy and invisible prims are skipped.
- **Joints**: physics joints become revolute, prismatic, continuous and fixed joints in
  `environment.urdf`, with one link per rigid body or referenced asset.
- **Materials**: OmniPBR, OmniSurface, OmniGlass and UsdPreviewSurface become glTF PBR
  materials with their textures. An unknown shader shows magenta and is listed in
  `conversion_report.json`.
- **Textures** are embedded in each GLB that draws them, scaled down to 1024 pixels on
  their longer side (`--texture-size PIXELS`, `0` keeps every image at its own size).
  A UDIM set (`<UDIM>` in the file name) of several tiles becomes one atlas image that
  the GLB reads through `KHR_texture_transform`.
- **Glass** (OmniGlass, and OmniSurface with transmission) is drawn as a blended,
  reflecting surface, since the viewer cannot refract; the authored transmission,
  index of refraction, base colour and metalness stay in the GLB material's `extras`.
- **Emission** is scaled by intensity / 1000 nits; anything brighter is stored as
  `KHR_materials_emissive_strength`. Glowing surfaces do not light the scene.
- **Lights**: the brightest distant light, the brightest dome light and every rect
  light go to `lighting.json`. Disk, sphere and cylinder lights are dropped.
- **Animation**: the stage's time samples, or the `--trace`, become joint values per
  frame; moving free bodies become objects. The import replays the result against the
  recording and writes the differences to `residuals.csv`.
- **Semantics**: `sdt:` and `ont:` attributes go to `semantics.json` and to the extras
  of the GLB nodes.
- **Collisions**: every geometry prim with an enabled `PhysicsCollisionAPI`, whatever
  its purpose or visibility (Isaac twins keep their colliders as guide cubes), becomes
  a `<collision>` box of its link: the box that bounds the prim in its own frame, exact
  for a cube. `conversion_report.json` counts them (`collisions`).

**Free objects.** With `--free-objects`, every standalone rigid body of a scene without
playback becomes a free object a CRAM world can pick up: a body the physics engine
moves (rigid body enabled, not kinematic) that no authored joint touches, nor a body
nested in it. It carries the bodies nested in it (the goods in a tote) and their
collision boxes, and gets a URDF of its own, `objects/<key>.urdf`, holding its GLB and
its boxes; the trajectory holds it at its spawn pose and `semantics.json` places the
prims it carries on it. Its key is its body's prim name; bodies that share a prim name,
such as chairs and books whose bodies are all `Geom/body`, are named after the elements
of their paths that differ (`/Root/Assets/chair/Actor_0000/Geom/body` becomes
`chair_Actor_0000`). Switched-off and kinematic bodies and articulations stay fixed
links of the environment. A playback decides its objects itself, so `--free-objects`
with a trace or time samples is refused.

### Export a twin

`cramera-export` hands the environment of a twin to a shared scenes directory
(`--out`, else `$CRAMERA_SCENES`, else `~/cramera_scenes`). It takes every option of
`cramera-import`, requires `--environment-name`, converts into a staging directory
`<out>/.export_<name>.*` and publishes the bundle there by rename only when it passes
the gate; `index.json` is then rebuilt. A rejected bundle stays in the staging directory
for inspection, and the published bundle and the index are unchanged. The gate demands
no unmapped material inputs and no missing textures, no robot model, something left out
(`--no-robots` for a twin without robots), every `--exclude` reported, no detached body
that still moves parts, no semantic prim below a left-out prim, and an index entry with
the environment name and without a robot.

| Option | Meaning |
| --- | --- |
| `--focal MM` | focal length of the twin's render view, passed on as the vertical field of view: `2 * atan((36 * H / W) / (2 * MM))` |
| `--render-size W H` | the size the render view is rendered at (default 1920 1200) |
| `--no-robots` | the twin has no robots |
| `--replay` | a replay of a recorded run: allows `--trace`, `--robot`, `--record`, `--fps` and `--keep-robots`, which are refused otherwise, and skips the environment checks |
| `--shot PNG` | screenshot the published bundle in headless Firefox; needs the viewer, `cramera` |

The conversion runs as a process of its own, capped at 8 GB where `systemd-run` exists
(`CRAMERA_IMPORT_MEMORY`, `0` for no cap), with
`USD_SHADE_MATERIAL_BINDING_API_CHECK=allowMissingAPI`, which silences the thousands of
material binding warnings Isaac stages print.

### Audit the robot separation

`cramera-audit-robots STAGE [--robots /World/Robots] [--no-robots] [--json FILE]`
checks statically how a composed twin separates its robots from its environment and
exits 1 when a check fails:

| Check | Demand |
| --- | --- |
| R0 | the stage marks at least one robot root (with `--no-robots`: none; R1, R5 and R6 are then skipped) |
| R1 | the root layer sublayers `<twin>_robots.usda` and `<twin>_environment.usda` next to it |
| R2 | every robot root is a direct child of the robots scope |
| R3 | every child of the robots scope is a robot root |
| R4 | every robot root carries `sdt:entityType` `Robot` |
| R5 | the environment layer, opened on its own, holds no robot |
| R6 | the robots layer authors prims only in the robots scope |
| R7 | no joint outside the robots connects to a robot body |
| R8 | every robot root has geometry |

## Develop

```bash
python3 -m venv ~/.virtualenvs/cramera-dev
~/.virtualenvs/cramera-dev/bin/pip install -e ./packages/protocol -e ./packages/viewer -e './packages/usd[test]' -r requirements-development.txt
cd packages/viewer && ~/.virtualenvs/cramera-dev/bin/python -m pytest -q --cov=cramera test
```

- Each package is tested on its own; CI requires 85 % coverage. The browser tests
  run in Node.js through `test_browser_modules.py`.
- [`tests/end_to_end`](tests/end_to_end/README.md) runs the whole stack with the
  CRAM workspace and ROS 2. A plan composed in the plan builder for a PR2 in the
  apartment is exported, run and replayed, and coraplex's apartment demo is shown live,
  saved and replayed. Run it with `python -m pytest -q -rxX tests/end_to_end` after
  `scripts/install_stack.py`. It takes about six minutes; CI skips it.
- Format with `python scripts/format_python.py` (black, then docformatter where
  black keeps its output). CI fails when it would change anything.
- `scripts/vendor_browser_libraries.py` updates three.js and urdf-loader, checked
  against the npm registry's integrity hashes.
- The code follows CRAM's
  [AGENTS.md](https://github.com/cram2/cognitive_robot_abstract_machine/blob/main/AGENTS.md)
  and the Code of Conduct in CRAM's README: dataclasses, one `StrEnum` for every
  fixed name, reStructuredText docstrings with one docstring per field, no
  `getattr`, no exception handling for control flow, a failing test before every
  fix.

The demo site is rebuilt by [`pages.yml`](.github/workflows/pages.yml) on every
push to `main`. A build whose viewer does not show its scene fails, so the site
keeps the last working build.

## Licence

cramera was developed by [Vanessa Hassouna](https://github.com/sunava) for AICOR
Solutions. Its licence is being changed to BSD-3-Clause (branch `license-bsd3`);
until that change lands, the code is distributed under GPL-3.0 as before. The
bundled three.js (MIT) and urdf-loader (Apache-2.0) keep their own licences, see
`packages/viewer/src/cramera/web/vendor`.

# Changelog

What landed in cramera, by week and area. Entries are features, not commits; hashes
in entries up to week 43 point into the `cramera-port` branch of the CRAM fork, from
which this repository was mirrored until 22 Oct 2026.


## 1.0.0 · in progress since 22 Oct 2026

### Removed
- **The Tour, Knowledge, Steps, Plan tree and Chart states tabs.** The knowledge
  graph duplicated what EQL answers, the viewer's own plan tree and statechart tree
  duplicated the questions plugin's Plan and Statechart graphs, the timeline already
  marks the steps, and the Tour panel went with its browser module: `cramera-offline`
  keeps the storyboard and bundle check routes and its commands.
  `GraphViewName.KNOWLEDGE` stays in the protocol for the API.
- **The models panel, the laboratory, the workshop and the cable lab.**
  `cramera-models`, `cramera-laboratory`, `cramera-workshop` and `cramera-cable-lab`
  are gone with their panels and pages, their protocol modules
  `cramera_protocol.models`, `cramera_protocol.laboratory`,
  `cramera_protocol.workshop` and `cramera_protocol.cable_lab`, `PluginName.MODELS`
  and `PluginName.WORKSHOP`, and the vendored Plotly. The navigation group
  `laboratory` stays for plugins outside this repository; `cramera_protocol.rights`
  stays for the scene builder.
  The compact medical laboratory is restored below; the expanded room remains
  removed.

### Added
- **Focusing the camera on a body.** Double-clicking a body in the 3D view selects it
  and glides the camera onto it: near enough to frame a small body, or onto the point
  hit for one too big to frame from where the camera stands, so a double click never
  moves the camera away. F frames the selection, and the scroll wheel zooms towards
  the cursor instead of the orbit centre (#107).
- **`cramera connect`: a live run on a remote server or in a container, in the
  laptop's browser.** Every page now reads the live publisher through the viewer's
  `/api/live/` relay, which also relays the page's changes (stopping, saving and
  discarding the recording, executing a resolution) for this machine, so the viewer's
  port is the only one to reach. `cramera connect user@server [--container NAME]
  [--start]` reaches it through the user's own `ssh`, carrying each connection into a
  container with `docker exec -i NAME cramera pipe`; nothing on the server listens
  beyond its loopback address. `cramera --lifeline` ends the viewer when its input
  closes, as `--start` uses it. `Option` and `DEFAULT_PORT` moved to
  `cramera.viewer_options`; `RELAYED_CHANGES` maps each change to its `ChangeReach`.
- **The Segmind tab: what segmind saw, annotated in the replay.** A run recorded with
  segmind watching (`cramera-onboard --detect-events`, or `CRAMERA_DETECT_EVENTS=1` for
  a live run) lists every event with its sort, the frame it happened at and the keys
  of its objects (`DetectedEvent.kind`, `frame` and `keys`). The new Segmind tab lists
  them in order, live as they are detected (`LiveRoute.DETECTED_EVENTS`,
  `Capability.DETECTED_EVENTS`) and in the replay: choosing one lights its objects up
  and shows its moment, the event the playback passed last is marked and, on request,
  lights its objects up, sorts of event can be switched off, and the timeline ticks
  each event shown under the slider.
  `cramera-bridge` now runs segmind the way segmind segments a run of its own, over the
  run's objects with its detectors of every kind, so a demo whose objects hang from
  fixed connections, such as the PR2 laying a place setting, records its grasps,
  pick-ups, placings and containments too; its own attachment detector is gone, since
  segmind reads pick-ups from grasps. A trimmed recording keeps the events of its
  frames. segmind's event classes are pinned to their sorts in `cramera_bridge.names`.
- **Every robot task demo steps through its queries.** `RobotTaskDemo` and its
  `TaskCommand` take `--step-queries`: the run is published from before its setup and
  held at each query of its own (`--no-hold` runs on) until **Next** moves it on, and
  `--name` names the recording. A task's own queries are those its code asks outside
  the CRAM workspace and the parts its plan leaves open, which coraplex grounds
  through the context's `query_backend`; what CRAM asks for itself, such as a robot
  part finding its robot or the world reasoner's rules, holds nothing. A step names
  the action under way, and a stepped recording is stamped with a clock that stands
  still while the run is held, so its replay keeps the run's pace. The stepping moved
  from the PR2 demo into `cramera_bridge.query_stepping` (`QueryStepper`,
  `SteppedBackend`, `ActionProgress`) and `HeldQueries`
  (`cramera_bridge.live.query_steps`). `VesselTransferDemo` finds its robot, the
  bench's handles and its vessel by entity queries, and
  `python -m cramera_bridge.demos.query_stepping --bundle DIR` runs it with
  `--step-queries`. `cramera_bridge.demos.garmi_apartment` is CRAM's GARMI
  demonstration as a task demo: GARMI carries a bowl and a spoon across the apartment,
  each transport grounded by a query.
- **A plan database in every recorded bundle.** Saving a recording keeps the run in
  the bundle's `plans.sqlite` (`BundleFile.PLAN_DATABASE`): the documents that change
  from run to run (scene, trajectory, statecharts, query steps, acceptance metrics and
  recorded answers), every plan step with the parameters and constraints the plan view
  shows, and the performed coraplex plans as CRAM's own database mapping stores them,
  where the workspace has generated it. Meshes and URDFs stay the bundle's files, kept
  once. A save with `addsARun`, and `cramera-onboard`, the task demos and the query
  stepper with `--add-run`, record a further run into the bundle of the same name: its
  files show the newest run, and its database keeps every run. The database is not
  committed.
- **Watching from a phone.** `cramera --lan` listens on every address and names the
  address, with a key, that other devices of the network open; they need the key, kept
  in `~/.cramera/network_key` and then in a cookie, and may only read, so asking,
  running and saving stay with the machine that serves the viewer. On touch screens
  the 3D view names its gestures, the scrubber and its marks are larger and **Pop
  out** is gone; the scene picker wraps on phones, and a phone on its side stacks the
  columns and fits the 3D view and the playback controls onto its screen.
- **Following a run from a phone.** A page opened at the viewer's network address
  reads the live publisher on `127.0.0.1:8765` through the viewer's relay below
  `/api/live/` (`ViewerRoute.LIVE_RELAY`), so `?live=http://127.0.0.1:8765` and the
  Live tab's default address work from a phone of `cramera --lan`. The relay forwards
  only the observation routes the page reads, each with its own query parameters,
  answers 503 when no publisher runs, and relays nothing that changes the run; the
  publisher still listens on this machine only.
- **Starting runs from a phone.** The new plugin `cramera-scene-runs` adds a **Runs**
  tab when `CRAMERA_SCENE_RUNS` names a file of runs (`SceneRunCatalog`: each run's
  identifier, label, command and an optional recording to replay). The tab starts one
  run at a time as a process of its own, with `{recording}` in its command naming a new
  recording, has the page follow it live, stops it with its whole process group, shows
  whether it recorded, failed or was stopped with the end of its output, and replays the
  recording. Its start and stop may be used by the pages behind a password-checking
  proxy.
- **Serving behind a password-checking proxy.** `cramera --public-origin ORIGIN`
  serves the viewer to a reverse proxy on the same machine, such as nginx with Basic
  auth at `/phone/`: every request counts as another device's that the proxy let in,
  so none needs the key. Routes say what such devices may do (`Route.remote_changes`,
  `Route.local_only`, also on plugin route declarations): behind the proxy they step a
  live run's queries through the relay (`/api/live/query_steps/next` and `/mode`) and
  use the routes a plugin offers them; everything else that changes something stays
  refused, and the plan builder's folder listing is for this machine only.
  `ViewerEvent.LIVE_REQUESTED` lets a plugin have the page follow a live publisher.
- **The scene graph lights up what it names and links it to the ontology.** Choosing
  a node in the Scene graph tab makes the meshes it stands for glow in the 3D view,
  inside one box per model and one per free object instead of a box per link: a
  model lights up all its links, the Free objects and Robot parts groups all their
  members. **Focus** moves the camera onto the node. A scene names the class of each
  entity in the world it was recorded from (`SceneDescription.classes`: the world,
  each robot model, body, connection and robot part, such as `PR2LeftArm`, `Body` or
  `RevoluteConnection`), and the node links each class to the Ontology page, which
  opens cram_owlviz at the class its fragment names (`ontology.html#package:Class`).
  `cramera-bridge` names the classes when it writes a bundle; `cramera-record-classes`
  names them in bundles written before, as CRAM reads each bundle, and the demo
  scenes carry them.

  ![The PR2's right gripper chosen in the scene graph, glowing, with its class link](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/scene-graph-ontology/scene-graph-part-focused.png)
  ![GarmiLeftGripper opened on the Ontology page from its class link](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/scene-graph-ontology/scene-graph-ontology-class.png)

- **The scene graph shows the world's semantic annotations.** A **Semantic
  annotations** group lists the annotations of the scene's world other than its
  robots and their parts, such as `Drawer`, `Handle`, `Door`, `Cabinet` or `Fridge`.
  Choosing one makes its bodies glow in the 3D view; its node refers to the link and
  free object nodes of those bodies and links its class to the Ontology page. A scene
  names them in `SceneDescription.annotations` (`SceneAnnotation`: its name, its
  class, `module.Class`, and its bodies by link name or free object identifier; None
  for a scene recorded without them). `cramera-bridge` names them when it writes a
  bundle; `cramera-record-classes` names them in bundles written before, from the
  world CRAM builds from each bundle. The demo scenes carry them: the GARMI
  apartment's kitchen cabinet with its four drawers, four doors and their handles, a
  sofa, a coffee table and a wall, and the medical lab's drawer.

  ![The GARMI apartment's kitchen cabinet chosen in the scene graph, glowing with its drawers, doors and handles](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/scene-graph-annotations/garmi-kitchen-cabinet.png)

- **GARMI's pick and place in an apartment as a demo scene.**
  `demo/standard_scenes/garmi_pick_place` replays GARMI picking up a bowl and then a
  spoon, 1 min 8 s, taken from the pre-1.0 viewer at cramera.informatik.uni-bremen.de
  and rewritten by `cramera-upgrade-bundle`, with recorded answers, so
  `cramera --demo-scenes demo` shows it without CRAM. The recording has its steps on
  the timeline but no plan or statechart.

  ![GARMI in the apartment, replayed from demo/standard_scenes](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/garmi-pick-place/garmi_pick_place_replay.png)

- **A recorded run's queries, stepped through in its replay.** A recording keeps the
  query steps its run published in `query_steps.json`, named by the scene's
  `querySteps`, and the Queries panel of its replay steps through them with **Next**,
  each query with its code, question, answers and what they light up, without CRAM or
  a running process. `cramera_bridge.demos.query_stepping` runs the PR2's vessel
  transfer at the bench held at every query it asks, and saves it with its queries;
  its recording ships as the demo scene `aicor_medical_lab_pr2_query_stepper`.


- **Collision boxes and free objects from USD stages.** `cramera-import` writes every
  enabled USD collider as a `<collision>` box of its link (`ReportCount.COLLISIONS`),
  and with `--free-objects` the standalone rigid bodies of a static scene become free
  objects carrying the bodies nested in them, each with a URDF of its own in
  `BundleFile.OBJECT_MODELS` (`ObjectDescription.urdf`); `cramera-export` passes
  `--free-objects` on. `BundleEnvironment` builds an object with a URDF from it, free
  on the world's root, its meshes found beside the URDF wherever the process runs
  (`SceneEnvironment.parse_model`), and annotates the prims the object carries
  (`PrimEntry.model`) on its body, so a CRAM plan can pick up an imported Isaac twin's
  totes or plates.

  ![CRAM's FR3 about to pick up a plate from an imported Isaac lab cell](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/collision-boxes-free-objects/fr3_pick_at_l09.png)
  ![The FR3 carrying the plate on, every step with its recorded parameters](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/collision-boxes-free-objects/fr3_place_into_h4.png)

- **Task demos of several plans.** `RobotTaskDemo.build_plans` returns the plans a task
  performs one after another, the task's plan alone by default; `run` publishes and
  records them all, so one robot carrying totes and another refilling them make one
  recording with a plan tree each.

- **How a plan's open parts were resolved, played beside the running robot.** A
  process that leaves parts of a plan open publishes each resolution through
  `LivePublisher.resolutions`: every open part asked as a statement, the backend that
  answered it (perception, working memory, rules or a probabilistic model), its
  answers and how the backend reasoned. The new **Plan resolution** panel plays each
  one part by part: the plan as stated, the backend's reasoning drawn (the images
  seen and how well each outline fits, the contacts tested, the cascade of rules
  walked to the one that fires with the layout it picks from, the distribution
  sampled), and the answer filling the resolved plan, while the scene lights up what
  the answers are about, draws regions as boxes and glides the camera onto them.
  Where the process offers it (`ResolutionExecution`), **Carry it out** has the robot
  perform a resolved plan.
- **Stepping through every query the robot asks.** A process publishes the queries
  it asks while it sets up, resolves, expands and carries out its plans through
  `LivePublisher.query_steps`, and, with a `QueryStepControl`, can be held at each
  query until a person moves on. The new **Queries** panel lists the latest queries
  and inspects one: where it is asked (the plan, actions and queries it is asked
  inside, down to its variables), the code that asks it, the question in English, the
  backend answering it, its answers and the backend's reasoning. **Next** moves the
  robot on only from the step the viewer saw, so a second click cannot skip one.
  `LivePublisher.scene_highlight` turns the bodies, annotations and regions an answer
  is about into what the viewer lights up.
- **The statement a query was answered by.** A query answered by a
  `TranslatedEvaluation`, such as one running on a database, carries the statement in
  the other query language, and the questions panel shows it under the query. A query
  can also be read back opening with how its backend answers it ("Generate …").

- **E57 laser scans in the scene builder.** **Load a point cloud** also takes `.e57`
  files, the exchange format of terrestrial scanners, of up to 64 GiB
  (`SceneBuilderLimits.maximum_scan_bytes`). The upload is written to disk as it
  arrives, every scan is placed by its pose, and its points are sampled and thinned on
  a voxel grid to the builder's two million, kept with their colours, or their
  intensities as grey, as a PLY point cloud. Converting needs the scene builder's new
  `e57` extra (pye57 and numpy); without it the upload is answered with what to
  install. New are `ScanFormat` in the protocol and, in the viewer, routes that stream
  their request body (`Route.streams_body`, `Request.stream`, `BodyStream`), so an
  upload larger than memory is never read into it.


- **A longer PR2 demo on the medical lab page.** **PR2 lab workflow (CRAM)** re-sorts
  the rack, stores a vessel in the refrigerator and loads a balanced pair into the
  centrifuge, opening and closing the refrigerator's door and the centrifuge's lid and
  driving smoothly between its stands at the bench; the page runs, follows and records
  it like the vessel transfer, one demo at a time. Like the MuJoCo laboratory, it takes
  a vessel for the refrigerator from the front with level fingers and pushes it in
  with the base, and a vessel for the centrifuge from above with the tool leaning 30°
  forwards about the fingers' closing direction, so the vessel's bottom clears the
  rim. It pulls the lid open by its handle and pushes it on from below, since the
  lid's hinge sits too far back for the PR2 to open it by the handle alone. Each robot
  demo answers on its own routes, `LaboratoryDemo.route(DemoRoute…)`
  (`transfer/state`, `workflow/state`, …), replacing `LaboratoryRoute.TRANSFER_*`;
  `LaboratoryTask.TRANSFER` is now `LaboratoryTask.COMMAND`, which takes `--demo`.
  The vessel handling the two demos share is `cramera_bridge.laboratory.handling`,
  where a `VesselGrip` says how a vessel is taken, lifted and set down; driving,
  tucking the arms with the base held and moving a door or a lid are
  `cramera_bridge.laboratory.mobility`.

  ![The medical lab page offering both PR2 demos](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/pr2-lab-workflow/lab_page_two_demos.png)
  ![The PR2 setting a vessel into its new rack slot](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/pr2-lab-workflow/rack_re_sort.png)
  ![A vessel stored in the refrigerator's upper shelf](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/pr2-lab-workflow/refrigerator_storing.png)
  ![The PR2 lowering a vessel into a centrifuge holder](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/pr2-lab-workflow/centrifuge_loading.png)
  ![The gripper above the centrifuge's rotor, leaning forwards](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/pr2-lab-workflow/centrifuge_holders.png)

- **A recorded PR2 run in the medical lab as the repository's demo scene.**
  `demo/standard_scenes/aicor_medical_lab_pr2_workflow` replays the PR2 re-sorting
  the rack and opening and closing the refrigerator door and the centrifuge lid,
  5 min 15 s at the simulation's 20 frames per second, with its plan steps and
  recorded answers, so `cramera --demo-scenes demo` shows it without CRAM.

  ![The PR2 lab workflow replayed from demo/standard_scenes](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/demo-standard-scenes/pr2_workflow_replay.png)


- **The demo site is built for every pull request.** The site's build and screenshot
  run on pull requests too, without publishing, so a change that breaks the site fails
  before it is merged; a pull request's build never cancels the publishing of `main`.
  `cramera-export-site` refuses a scenes directory without a readable bundle
  (`NoScenesToExport`) and a `--default-scene` it does not export
  (`DefaultSceneNotExported`) before writing anything, instead of writing a site whose
  screenshot step waits 180 s for a scene that never comes.

  ![cramera-export-site refusing a missing scenes directory and a default scene it does not export](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/site-export-checks/export-error.png)
  ![The demo site built for a pull request, its deploy skipped](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/site-export-checks/pr-pages-job.png)
- **A dedicated small medical lab page with the local PR2 demo.** The top-bar
  **Medical lab** link opens its own 3D view, controls and timeline. With the full
  CRAM stack and ROS robot descriptions, it starts with the compact workbench and
  the real PR2, follows a new vessel transfer live and opens its recording on
  completion. Further runs preserve earlier recordings. The robot demo uses CRAM's
  kinematic simulation through `cramera-bridge`.
  The separate MuJoCo contact simulation keeps the liquid handling, scale,
  centrifuge and manuals, refrigerator, drawer and dispenser controls, with setup
  help inside the page. Only the small scene and its five device bundles are
  shipped. The stack installer includes the page; the viewer stops its simulation
  and owned transfer process on shutdown. See [the setup guide](docs/local-medical-lab.md).
- **Drop-downs for a plan step's handle, drawer, door and other annotations.** An
  annotation field of the plan builder offers the chosen environment's annotations of
  its class without a live scene, and the live world's surfaces while it runs. An
  environment without prim semantics, such as one loaded from a URDF, gets the
  annotations CRAM's `WorldReasoner` infers when its world is built, for a plan run and
  the live scene alike, each named after the body it is rooted in
  (`handle_cab1_top_door`) so a plan can name it. New are the route
  `PlanBuilderRoute.ENVIRONMENT_ANNOTATIONS`, the document `EnvironmentAnnotations`
  and `cramera_bridge.scenes.inferred_annotations`.

  ![The handle drop-down of an Open step in the apartment](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/environment-annotations/handle_dropdown.png)
- **A picker for the plan builder's URDF environment.** **Browse…** next to the URDF
  path field opens a list of the viewer's machine's folders and their URDF and xacro
  files, hidden ones left out; a click on a file loads it as the environment. The picker
  starts in the home folder and later reopens on the folder it showed last. New are
  the route `PlanBuilderRoute.URDF_FOLDER` with `UrdfFolderParameter.PATH` and the
  document `UrdfFolder`.

  ![The URDF picker on CRAM's worlds](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-56-picker/picker_worlds.png)
- **A URDF of this machine as the plan builder's environment.** Below the environment
  list, a path field and **Load URDF** take a URDF or xacro file, such as CRAM's
  `coraplex/resources/worlds/apartment.urdf` or `kitchen.urdf`. It is copied with every
  mesh it names into a new bundle of the local scenes, offered among the environments
  and chosen; meshes that cannot be found are named and left out. New are the route
  `PlanBuilderRoute.ENVIRONMENTS`, the documents `UrdfEnvironment` and
  `LoadedEnvironment`, and `cramera_bridge.export.urdf_environment`.

  ![CRAM's apartment loaded as the environment](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-56/apartment_loaded.png)
- **A map of the world's axes in the plan builder.** The 3D view shows a labelled
  x, y and z triad at the origin, where the robots' and objects' poses are measured
  from, drawn over the robots so one standing at the origin does not hide it, and a
  compass in the bottom left corner that turns with the camera, so x, y and z read
  the same way the pose fields do from any view. Red is x, green y and blue z, as the
  viewer's frame axes. The map is `SceneAxes` in the builder's `scene-axes.js`.

  ![The plan builder's axes at the origin and the compass](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-53/after.png)
- **Primitive shapes in the scene builder.** The library's new **Shapes** category
  offers a box, a cylinder and a sphere (`primitives/box`, …), one metre across, so
  their size is set in metres. Every placed instance can carry a description of what
  it is and how it works, saved with the draft (`InstanceKey.DESCRIPTION`), so the
  object a shape stands for can be designed later.

  ![Placing, sizing and describing shapes](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-41/walkthrough.gif)
- **Objects of the meshes library in the plan builder.** `resources/private_meshes` is
  a submodule of [sunava/private_meshes](https://github.com/sunava/private_meshes), and
  the builder offers every model of it, and of any library `CRAMERA_OBJECT_LIBRARIES`
  names, beside the meshes of coraplex: its URDF is drawn in the 3D view and merged
  into the demo's world as a free, graspable object.

  ![A library object in the plan builder](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-17/builder.png)
- **A film of a recording.** The timeline's video button films the shown recording as a
  WebM with a cinematic camera: it cuts between wide shots and close-ups on every pick
  and place, slows down around them and saves the film when the recording ends; the
  layer *Cinematic camera* shows the same shots while playing.

  ![A filmed pick-up](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-13/film.gif)
- **The live view says what it is doing.** A badge in the top bar shows whether the
  viewer is off, connecting, live or has lost the publisher; the Live tab names what
  the publisher offers and whether it records, the timeline says whether it follows
  the run or waits for one, and an ended run offers *Save this run…*.

  ![The live view's states](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-15/states.gif)
- **The Perception tab.** Every perception step of the shown recording's plans, or of
  the live plan, with what it was asked (its action's parameters), how it ended, why
  it failed, and the objects it reported, which light up in the 3D view. The bridge
  records a step's reported objects (`perceived`) and any node's failure reason
  (`reason`) on the plan's nodes, and names a class parameter by its class.

  ![The Perception tab](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-25/perception.png)
- **What perception saw.** A perception step in the Perception tab shows the images of
  what it saw, also when it failed: in simulation the robot's camera view drawn from the
  world model, with the reported objects outlined, and its depth; on the real robot also
  every image RoboKudo's visualizer publishes on `<pipeline>/<annotator>/output_image`
  while the step runs. A live plan's node lists them in `images`, each served by the
  publisher's `/perception_image` route; a recording keeps them as PNG files in its
  `perception/` directory.

  ![What a perception step saw](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-25-images/perception_tab.png)
- **The Scene graph tab.** The scene page browses the shown world as a tree, the way
  the Ontology tab browses classes: its models with their links nested by joint, its
  free objects and the robot's part annotations. A node shows its fields, what it
  depends on and what refers to it, each a click away, and lights up in the 3D view;
  clicking a part in the 3D view chooses its node.

  ![Browsing the scene graph](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-24/walkthrough.gif)
- **Another logo in the top bar.** `cramera --logo FILE` (or `CRAMERA_LOGO`) and
  `cramera-export-site --logo FILE` show another image instead of the AICOR logo on
  every page, served at `/api/logo`; `--logo-text` says what it shows.

  ![Another logo in the top bar](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-27/custom_builder.png)
- **One command installs the whole stack.** `python3 scripts/install_stack.py` puts the
  viewer, every plugin, `cramera-bridge` and the CRAM workspace (cloned next to the
  checkout when missing) editable into one uv environment that sees ROS 2's Python
  packages, leaving out CRAM's own earlier `cramera` so this repository's viewer is
  installed. `--viewer-only`, `--with-usd`, `--dev` and `--dry-run` choose less or more.
- **End-to-end scenarios.** `tests/end_to_end` runs the whole stack with the CRAM
  workspace and ROS 2. A PR2 plan in the apartment is composed through the plan
  builder's routes, saved, exported, run and replayed. coraplex's apartment demo runs
  unchanged under `cramera-live`, is checked while it runs, then saved and replayed.
  CI skips the scenarios; `tests/end_to_end/README.md` says how to run them.

  ![The plan builder's run replayed](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-33/apartment_PR2_pr2_apartment_pick_85.png)
- **Recorded plans keep every step parameter and constraint.** Every plan node the
  live publisher describes renders each field of its designator by name
  (`LivePlanNodeKey.PARAMETERS`): an enumeration member by its name, an object by its
  key, a pose by its position, a switch as on or off, a nested description by its
  fields. A recording's plan tree stores them (`PlanNodeKey.PARAMETERS`) together
  with the constraints attached to each node and their last observation
  (`PlanNodeKey.CONSTRAINTS`, `PlanConstraintKey`). Recordings written before keep
  showing the arm and target they have.

- **Resizable panes and a foldable query slot.** A divider between the scene and
  the side column drags to give either side more width (a quarter to three quarters,
  double-click for halves), a divider below the query slot drags to give it more or
  less of the column, and the query slot's head has a button that folds it to its
  heading so the panels take the column. All three are remembered in the browser
  under `PageStorageKey.SPLIT`, `PageStorageKey.QUERY_SPLIT` and
  `PageStorageKey.QUERY_FOLDED`.
- **Scenes directories inside scenes directories.** A child of a scenes directory
  that is not a bundle but holds an `index.json` or bundles is a further scenes
  directory of the same source, and so on below it. The repository's own demo bundles
  moved to `demo/standard_scenes`; `cramera --demo-scenes demo` shows them with every
  scenes repository cloned next to them, which git ignores. The scene builder names
  such a nested library root `<source>-<directory>`.
- **A new viewer replaces the old one.** `cramera` started on a port another cramera
  viewer holds stops that viewer and takes the port; `--keep-running` keeps the
  earlier behaviour of failing with *address already in use*.
- **Robots as their models in the plan builder.** The 3D view draws each robot of a
  plan as its model at rest instead of a disc. cramera-bridge writes a robot's URDF or
  xacro with its meshes into the plan builder's data directory the first time it is
  asked for (`robots/<robot>/<robot>.urdf`); a robot whose description is not installed
  keeps its disc. The packages are found as CRAM finds them, so the viewer is started
  with the ROS workspace sourced.

  ![PR2 in the plan builder](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/plan-builder-robots/builder.png)
- **The Unitree G1 stands on the floor in the plan builder.** Its root is its pelvis,
  so a G1 added to a plan starts 0.7923 m up (coraplex's `PELVIS_HEIGHT_ABOVE_FLOOR`)
  instead of sinking through the floor; other robots stay at 0. The catalog's robot
  models carry the height (`RobotModelKey.STANDING_HEIGHT`), and moving or turning a
  robot in the builder keeps its height.

### Fixed
- **The live view draws the ROS debug markers again.** The publisher read and served
  them, but no part of the v1 viewer asked for them. The page now reads the overlay
  whenever a state names a new version, through the viewer's relay, and draws every
  marker kind the way RViz does: arrows by scale or between two points, boxes,
  spheres, upright cylinders, line strips and lists as solid lines, box and sphere
  lists, points with their own colours, and texts facing the camera. A marker that
  only moved is moved, not built again. *ROS markers* in the Layers overlay switches
  them, and the Live panel chooses the topics read and the namespaces drawn. Markers
  that arrive while the world rests now take a snapshot; the last of a burst is taken
  when the page next reads the state. A marker given in a body's frame follows the
  body: the state carries its pose, so the overlay is not read again while it moves.
  A marker whose quaternion is all zero, as publishers that set no orientation leave
  it, is drawn unrotated instead of failing the snapshot (#114).
- **A live run shows the kitchen's joints where the physics has them.** While a plan
  runs, a MuJoCo synchronizer can leave the joints that are not the robot's to the
  physics, and the world then keeps its own positions of them: the viewer showed a
  drawer or door standing still while the robot pushed it, and jumping when the plan
  ended. A snapshot now takes those joints' positions from every synchronizer that
  gives them, through `physics_positions_left_to_the_physics` (#111).
- **A saved scene's Statechart tab draws its recorded statecharts again.** Since the
  Chart states tab was dropped, the tab only said the scene held no statechart, even
  when its bundle had one. It now draws the chart of the shown frame as a tree,
  coloured by each node's life cycle, and follows the playback. CRAM's internal nodes,
  such as the collision avoidance's task per link, are folded into the node above
  them, whose details count them by life cycle (#110).
- **A live run records only settled states, at most 30 a second.** The live publisher
  took a full snapshot of every state change, so the planner's checks, which set a
  state and restore it, slowed the run down and landed in the recording as poses the
  robot never took. While a plan performs, snapshots are now taken after the
  executor's ticks, never from a state change itself; outside a plan, at most one per
  frame interval. The last state of a plan, and of a recording when it ends, is always
  kept (#100).

- **Saving a run right after stopping it saves that run.** The recording counted as
  ended before its bundle was written, so a save in those seconds copied whatever
  `__recording__` bundle an earlier run had left, or found none. A save now waits for
  the written bundle, which the Live panel offers to save only once it is there, and a
  new recording deletes the unsaved bundle an earlier one left.

- **A world followed live draws its free objects.** The live bundle described none,
  so walls, sinks and fixtures loaded from MJCF appeared only in a saved recording.
  It now describes every free object at the pose it holds when the bundle is written,
  and an object added, removed or drawn anew rewrites the bundle.

- **Doors and drawers of MJCF worlds sit where the world has them.** The URDF export
  dropped a connection's pose of its child relative to the joint, so every MJCF hinge
  and slide child was drawn displaced by its joint's position. Such a joint now moves a
  link of its own at the joint frame, `<child>_connection_frame`, to which the child is
  fixed; the child link keeps its body's frame and the joint its name.
- **Textured surfaces keep their textures in the viewer.** A world loaded from MJCF,
  such as a RoboCasa kitchen, was drawn in flat colours. A primitive with a texture
  and a mesh painted with a texture its own file does not name are now written into
  the bundle as binary glTF holding the geometry, the texture coordinates and the
  image, in the URDF models and for the free objects alike (`TexturedSurface`); a
  primitive's texture repeats per meter or across each face, as the texture states.
- **Nothing comes between the cinematic camera and what it films.** The film's wide
  orbit and its close-ups could stand behind a wall, inside a cabinet or beyond a door,
  so the picture showed that and not the robot. The camera now casts rays against the
  environment (`LineOfSight`; the robots and the free objects are what it films):
  a blocked or crowded angle, one whose picture a near surface mostly hides, films from
  another one, the wide orbit turns back where a wall would come between and looks from
  higher up when it has to, a close-up films its object from the other side or from
  above, and a camera with nowhere clear to go stands in front of what is in the way.
  The camera eases round what it films rather than through it, moves in front of a
  surface at once rather than easing behind it, and cuts to the shot when easing would
  lose the robot behind a wall, as when it moves faster than the camera follows. A mesh
  of many triangles, such as an oven, stands in the way as its box, and a ray that starts
  inside that box, as at a spoon in a drawer, tests the mesh's triangles through a tree
  of nested boxes built the first time; in the PR2 apartment the camera then takes a
  millisecond or two a frame.

  ![Before and after in the PR2 apartment](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/cinematic-occlusion/cinematic_camera_occlusion.png)
- **Play NEEM jumps to the moment an answer is about.** An answer's replay windows
  named seconds since the epoch, which the questions panel took for frame numbers, so
  every replay, such as one of "what was detected, and when?", landed on the last
  frame. `ReplayWindow` now names the first and last frame of the recorded scene
  (`first_frame`, `last_frame`): the frames recorded nearest to a second before and a
  second after the moment, found from the wall-clock time of each frame of its
  trajectory (`FrameTimeline`). A moment outside the recording, an answer about a
  running world and a recording without frame times offer no replay. Windows recorded
  before in seconds offer none either; record the answers again with
  `cramera-record-answers` to replay them. The answer's button, **Replay** before, is
  called **Play NEEM**.

- **An environment drawn without collision geometry gets its annotations.** CRAM's
  world reasoner takes a body without collision geometry for one that only carries a
  joint, so the world built from a bundle whose environment has visual shapes only,
  such as the GARMI apartment the pre-1.0 viewer recorded, got no handles, drawers or
  doors. While the reasoner reads the world, such bodies now collide by their visual
  shapes (`InferredAnnotations`); afterwards they keep their own collision geometry.
- **Isaac apartments pass the export gate with their materials carried over.**
  `cramera-export` refused cram-vrb-lab's GARMI apartment for inputs it neither mapped
  nor kept. They now reach the GLB: OmniPBR's `specular_level` and UsdPreviewSurface's
  specular workflow become `KHR_materials_specular`'s `specularColorFactor`
  (`PhysicallyBasedMaterial.specular_color_factor`), which the viewer renders;
  OmniGlass's `reflection_color` scales the reflected share of the blended glass and
  is kept in its extras; OmniGlass's cut-out opacity scales its coverage; and an
  OmniPBR opacity texture becomes the alpha of the base colour texture
  (`ColorOpacityTexture`), so leaves and petals are cut out.

  ![The GARMI apartment's kitchen exported with its materials](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/apartment-materials/garmi_apartment_kitchen.png)
  ![Flowers cut out by their opacity texture in a glass vase](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/apartment-materials/garmi_apartment_flowers.png)

- **Isaac twins with NVIDIA SimReady items pass the export gate and load in the
  viewer.** `cramera-export` refused LabAutomationTwin's S2 lab once it held SimReady
  items: their OmniPBR and OmniGlass materials set `flip_tangent_u` and
  `flip_tangent_v`, OmniPBR sets `opacity_constant` without enabling opacity, and the
  hygiene station reads `<UDIM>` textures; its bundle grew to 2.9 GB. The tangent
  flips are read against MDL's defaults (`TangentFlips`), under which OmniPBR reads a
  normal map as glTF does: both flipped negate the normal scale, one flipped inverts
  that channel of the embedded normal map (`FlippedNormalTexture`), and without a
  normal map they have no effect, as `opacity_constant` has none while opacity is off.
  A UDIM set is resolved beside the layer that authors it (`TextureFile.tiles`): tile
  1001 alone stays one image, several tiles become one atlas image read through
  `KHR_texture_transform` (`TextureImage`, `TextureTransform.onto_atlas`). Embedded
  images are scaled down to 1024 pixels on their longer side (`--texture-size PIXELS`,
  `0` for none), since every GLB embeds the textures its link draws and the browser
  uploads them once per GLB: the SimReady items' 66 textures of 4096 pixels were
  embedded 247 times and needed 20.8 GB of texture memory. Each texture is encoded
  once per bundle (`TextureEncodings`). The S2 lab is 293 MB instead of 2894 MB and
  exports in 52 s instead of 5 min 44 s.
- **Free objects whose bodies share a prim name are told apart.** `--free-objects`
  keyed every object by its rigid body's prim name, so the GARMI apartment, whose
  chairs, books and dining table each keep their rigid body in a `Geom/body`, listed
  them as `body`, `body_2`, ... `body_14`. Bodies of one name are now named after the
  elements of their paths that differ (`ObjectNames`):
  `/Root/Meshes/Assets/chair/Actor_0000/Geom/body` becomes `chair_Actor_0000`; a body
  whose name no other has keeps it.

  ![The GARMI apartment's free objects named after their assets](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/free-object-names/apartment_object_names.png)
- **A live body drawn anew is bundled again.** The live bundle's signature now counts
  each body's shapes, so a body whose shapes change while the run goes on is shown
  with its new shapes instead of the old ones.
- **The demo site is published again.** Since the demo scenes left the repository,
  every build of `main` exported a site without scenes and failed its screenshot
  check, so the site kept the build of 23 Oct. It now shows the small medical
  laboratory that `cramera-laboratory` ships.

  ![The demo site showing the medical lab](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/demo-site/after.png)
- **The medical lab page says how a PR2 run ended.** A failed run reported only
  the end of its log, which often held nothing but startup warnings. Its error now
  starts with the signal that ended the process (such as `SIGKILL`), its exit code,
  or that it finished without saving its recording. A run stopped with **End the
  run** or on viewer shutdown is reported as `TaskRunState.ENDED` instead of
  failed, and can run again.

  ![A PR2 run ended with End the run](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/run-exit-status/ended_panel.png)
  ![A PR2 run whose process SIGKILL ended](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/run-exit-status/signal_panel.png)
- **A plan runs in an environment loaded from a URDF.** A bundle's URDF naming its
  meshes relative to itself was read relative to the process's working directory, so
  building its world failed (`string is not a file: …/meshes/environment/…`); its
  meshes are now found beside the URDF.

### Changed
- **cramera-bridge follows CRAM 26.10.** A plan runs as one cramph statechart, so
  the bridge follows it through executor extensions instead of plan callbacks:
  `CrameraVisualization` is the world visualization of coraplex's executors, and
  `CoraplexBackendRegistration` adds it to every plan executor a demo file creates for
  its world, which replaces the `CORAPLEX_VISUALIZATION` selection and the
  `coraplex.visualizations` entry point. The plan view shows the statechart's actions,
  composites, statements and the motions they expand into with their own life cycles,
  the statechart view the whole statechart. Robot task demos build their plans as
  statechart nodes with a `TaskContext` and perform them through `PlanPerformance`
  (`cramera_bridge.performing`); `RobotTaskDemo.executor_type` replaces
  `execution_type`. Spatial types come from `krrood_spatial_math`, worlds are created
  with `create_world()`, colours from RGBA.
- **The scene builder opens with an empty draft.** It no longer restores the draft of
  the last visit, so it starts without the scene opened before; only a reload, or the
  browser's back and forward buttons, continue the draft the tab was editing. The copy
  lives in the tab's `sessionStorage` instead of `localStorage`, so two builder tabs no
  longer overwrite each other's, and reloading a page that names a scene keeps its
  edits instead of starting the scene again. `BuilderEnvironment` takes `returning`.
- **The Medical lab page is called Physical lab.** Its top-bar link, page title,
  heading and panel say **Physical lab**; the page stays at
  `/plugins/laboratory/laboratory.html` and the scenes keep their names.
- **A new plan starts without a robot.** The plan builder no longer adds one to a new
  plan; the Robots section asks for one and its **Add** picks the model used last, else
  the one offering the most actions. The last robot can be removed again.
  `PlanState.empty` adds a robot only of a model it is given, and
  `PlanState.defaultModel` names the model **Add** picks.

  ![A new plan without a robot](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/no-default-robot/no_robot.png)
- **The protocol names actions, robots, plan node kinds, fields, enumeration members
  and surface types itself.** Plans name actions and robot models by snake_case
  identifiers (`pick_up`, `pr2`), enumeration members and surface types too (`high`,
  `table`), and recordings name plan node kinds as `action`, `motion`, `sequence`, …
  instead of CRAM's class names. `cramera_bridge/names.py` is the one table between
  the two; it also maps action fields and enumeration members CRAM renames, so a rename
  in CRAM changes only that table. Plans are written in format 1.3; plans of 1.1 and
  1.2 are translated through the names the catalog lists as saved before (`savedAs`,
  `savedMembers`, `savedSurfaceTypes`), and earlier recordings' class names are read as
  their kinds. A new plan still starts with the robot model used last when the browser
  remembers it by its old name.

  ![A plan sent with CRAM's names and saved with identifiers](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-49/saved_plan_values.png)

- **Things a robot takes hold of are objects from the start.** Every body that a
  semantic annotation of something with grasps describes (Milk, Bowl, Spoon, Cup, …) is
  published and recorded as an object, however the world attaches it. Parts of other
  things (handles, rims, doors, drawers, furniture) are not. A demo that fixes its
  objects to the environment, like coraplex's apartment demo, now shows them carried
  in the live view and the replay instead of standing still in the environment.

  ![Carrying the bowl in the replay](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-37/carrying.gif)
- **A sub-action is shown by what it matches.** A composite action's sub-action that
  coraplex leaves to be found, such as TransportAction's `pick_up`, shows as the
  action it matches and what it states about it (`MoveAndPickUpAction: navigate
  NavigateAction, …`) instead of the match's internal fields; a value left open shows
  as `any <type>`.

  ![A transport's sub-actions in the Plan View](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-38/after.png)
- **An action shows what its motions went through.** coraplex performs an action's
  motions without starting the action node itself, so recorded actions stayed
  `CREATED` and named no frames. An action coraplex left unstarted now shows the
  status of its motion that started last, running until its motions ran or the plan
  ended. Recordings then know when each step ran, and the Plan View follows the
  playhead again.

  ![The Plan View following the playhead](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-36/after.png)
- **The plan builder's environment and robots are clearer.** The builder has an
  Environment section and a Robots section with one card per robot; the card whose plan
  is edited is marked, picking a robot in the 3D view selects it, and every robot has
  its name above it.

  ![The builder's environment and robots](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-14/after.png)
- **The theme switch is in the top bar.** A Dark/Light switch beside the version
  replaces the View menu, which held only the theme.

  ![The theme switch in the top bar](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/issue-26/after_light.png)
- **The plugins' extra is called `bridge`.** `cramera-questions[bridge]` and
  `cramera-plan-builder[bridge]` name what they install, `cramera-bridge`; the
  former `cram` extra is gone.
- **cramera-cram is cramera-bridge.** The package that connects cramera to CRAM is
  `cramera-bridge` now, imported as `cramera_bridge`, in `packages/bridge`; the
  plugins' `bridge` extra installs it, coraplex still finds its visualization as
  `cramera`, and the live publisher reports itself as `cramera-bridge`. An
  environment that still holds `cramera-cram` registers that visualization twice, so
  uninstall it (`pip uninstall cramera-cram`); `scripts/install_stack.py` does.
- **Scenes are drawn in colour.** Every model loaded from a URDF is lit like the
  scene's own objects:
  - its URDF and COLLADA materials become physically based ones, keeping their colour,
    texture and transparency;
  - lights a model file brings are removed, and blown-out whites are toned down;
  - a COLLADA mesh without materials takes its URDF colour, as STL and OBJ meshes do;
  - an environment part that its description leaves uncoloured gets the colour of what
    its link names (countertop, table, cabinet, handle, wall, …), for any environment;
    authored colours always win.

  The scene builder draws its URDFs the same way. The URDF colours of bare meshes need
  the semantic_digital_twin fix on `sunava/cognitive_robot_abstract_machine`, branch
  `fix/urdf-mesh-colors`.

  ![The apartment and the PR2 in colour](https://raw.githubusercontent.com/AICOR-Solutions/cramera/pr-media/colors/after_pick.png)
- **The Plan builder panel is the Plan View.** The viewer's panel of
  `cramera-plan-builder` is named Plan View and holds only the replayed recording's
  plan: its step cards now show every parameter the step was recorded with,
  labelled as the builder labels the field, and each constraint as a chip with its
  monitor and last observation. The panel's description and its links to the
  builder page are gone; the page stays at `/plugins/plan-builder/builder.html`.
- **Only cramera-bridge imports CRAM.** The plan builder's CRAM side (action
  discovery, steps, arms, world, surfaces, the generated demo's command) moved from
  `cramera_plan_builder.cram` to `cramera_bridge.plans` with its tests; the builder
  reads its catalog as protocol documents and its demos import
  `cramera_bridge.plans.command`. `DemoOption` moved into `cramera_protocol.plan_builder`,
  `Lifeline` into `cramera.lifeline`, and the errors the CRAM side raises into
  `cramera_bridge.exceptions` with their status and message unchanged. Demos generated
  before import the old module and have to be generated again.
- **The question engine moved into cramera-bridge.** Everything of `cramera-questions`
  that evaluates, words or lists queries with krrood, and what it needs (presets,
  matching, the recorded bundle reader, graph views, the workspace class index), is
  `cramera_bridge.questions` now, with `cramera_bridge.questions.live` for live worlds
  and `cramera_bridge.questions.scenes` answering the plugin's routes in protocol
  documents. `cramera-questions` keeps the plugin, its routes and its browser module,
  depends on `cramera-bridge` only through its `bridge` extra, and no longer is a
  dependency of `cramera-bridge`. `cramera-record-answers` is installed by
  `cramera-bridge`. A test of `cramera-bridge` fails on any CRAM import outside it.

### A rewrite into packages
- **One protocol for everything.** `cramera-protocol` defines scene bundles, the
  scenes index, live messages, routes and versions once; the browser reads the same
  names from a generated `protocol.js`. Every recording on the development machine
  (62 scenes) reads without error, including those written before versions existed.
- **A viewer without CRAM.** `cramera` serves the browser application, merges local,
  shared and demo scenes, loads plugins through entry points, and answers only pages
  of the same machine. The page is rebuilt as ES modules on three.js 0.186.1.
- **A demo site that shows what is published.** Every push to `main` rebuilds the
  site with three demo scenes, stamps it with the commit and takes a screenshot that
  the status page and the README show.
- **USD import without the viewer.** `cramera-usd` converts a USD stage into a scene
  bundle (`cramera-import`), gates and publishes a twin's environment by rename
  (`cramera-export`) and audits how a twin separates its robots
  (`cramera-audit-robots`). On the test stages its meshes, URDFs, semantics and
  residuals are byte for byte those of the earlier converter; every JSON file of a
  bundle is written through a protocol document.
- **Live mode.** `cramera-live` runs a CRAM demo and publishes its world; the viewer
  opened with `?live=` follows its joint states, plan and statecharts without a
  recorded scene, and the Live tab stops, saves and replays the recording.
- **Questions and graphs.** The `questions` plugin fills the query slot with the EQL
  card, which answers presets, plain English and entity queries with the question
  read back and a table whose entities light up in 3D, and adds Knowledge, Plan,
  Statechart and Kinematics tabs, which draw the scene's graphs as SVG with pan, zoom,
  fit, fullscreen and node details. The plugin loads without krrood and then answers
  that it is missing.
- **The original look.** The page is laid out as the original AICOR viewer was:
  dark, the AICOR logo and the plugins' pages in the top bar, the scene card with the
  Layers overlay and the playback controls with coloured step, pick and release marks
  on the left, the question card and the graph tabs on the right; a light theme is
  in the View menu. Plugins declare their top-bar entry (`ViewerPlugin.navigation()`),
  and the builder and cable lab pages carry the same bar.
- **The CRAM ontology in the top bar.** The last entry of the top bar, Ontology, opens
  a page of the viewer that embeds cram_owlviz, the browser of the CRAM workspace's
  classes, below the same bar, with a link that opens it in a tab of its own. The page
  is part of the demo site.
- **Recorded answers on the demo site.** `cramera-record-answers` writes a bundle's
  preset answers and graph views into `recorded_answers.json` in a CRAM workspace;
  the static site includes the questions plugin (`cramera-export-site --plugin`) and
  shows them read-only.
- **Plan builder.** `cramera-plan-builder` composes a plan from the robots, objects
  and environments the CRAM workspace offers, turns sentences such as "keep the milk
  upright" into constraint monitors, writes the plan as a CRAM demo and runs it live,
  with its constraints shown on the steps.
- **Every CRAM action in the plan builder.** The builder discovers the action
  dataclasses of coraplex's actions package (26 in the development checkout, 23 of
  them available) and builds each step's form from the action's typed fields:
  objects, annotated entities, the robot's enumeration choices, poses, a pose or a
  surface, numbers, switches and texts, with a derived grasp and optional fields
  under "More options". An action whose required field has no form control yet is
  listed with the reason, and an action module that fails to import is named. Steps
  store the action and its values; plans saved with the earlier six step kinds load
  as the actions those kinds stood for, and a step that no longer fits its action is
  refused, with the step and field named, before its demo is written or run.
- **The plan beside the replay.** A recording names the frames each node of its plan
  ran in (`start` and `end`, from the deepest running action of every frame), and a
  saved stretch of a run keeps them counted from its first frame. When a recording is
  replayed, the plan builder's panel comes to the front and shows its plan as the
  builder's step cards, one list per plan, with each step's arm and target: the step
  at the playhead is marked running and kept in sight while the recording plays or
  is scrubbed, finished steps show the state they ended in, and a click on a step
  jumps to its first frame. Recordings written before name no step frames; their
  steps are shown with the state they ended in.
- **Every robot's plan in one recording.** A run that performs several plans, such as
  one robot carrying totes and then another refilling them, keeps every plan instead
  of only the last: the recording holds one plan tree per plan, in the order they
  began, and each root names the robot that performed it (`robot`, the robot's display
  name). The first plan's nodes keep their identities (`plan_node_<index>`), a later
  plan's nodes are `plan_<number>_node_<index>`, so every frame's running step and
  every live constraint names exactly one node. The replay titles each plan's list
  with its robot, and plans recorded without one by their number.
- **One browser test kit for plugins.** `cramera.testing` runs a plugin's Node tests
  against the viewer's modules as the viewer serves them, with a shared fake DOM.
- **Tools.** `cramera-shot` (screenshots once the page is ready),
  `cramera-export-site` (static site), `cramera-upgrade-bundle` (rewrite a bundle in
  the current format).
- **The laboratory as a plugin.** `cramera-laboratory` ships the bench and the
  expanded AICOR laboratory and their twenty devices with their asset terms, runs
  their MuJoCo contact simulation behind a Laboratory panel (bounded pulling, liquid
  with pouring and spilling, scale, centrifuge with operating manuals, refrigerator,
  drawer, dispensing station, instruments), loads the laboratory into CRAM worlds
  and records a PR2 vessel transfer as a task demo.
- **The wood workshop as a plugin.** `cramera-workshop` ships the AICOR workshop bundle
  with its asset licence and notice, runs its MuJoCo contact world with the ideal screw
  thread behind a Workshop panel, records the PR2 screw insertion as a task demo,
  runs the two-handed assembly through all its stages (not yet recorded: its arms still
  penetrate the furniture while driving and putting the screwdriver down), and checks a recorded assembly before it is published
  (`cramera-workshop check-release`). A screw is grasped the same way however far it
  rolled in its box, and a recording's steps are the stages of its plan. The asset
  rights document is shared with the laboratory as `cramera_protocol.rights`.
- **The cable lab as a plugin.** `cramera-cable-lab` simulates a MuJoCo composite cable
  whose capsules semantic CRAM points follow, with a friction gripper, on a page of its
  own; it owns its session instead of reusing the laboratory's. A cable MuJoCo cannot
  build is refused instead of being kept as points without physics.
- **Plugins release what they own.** `ViewerPlugin.close()` is called once the viewer
  stops serving; `Request.document()` reads a request body as a protocol document.
- The earlier code is kept on the branch `archive/pre-v1`.

## Week 43 · 20 Oct 2026

### Repository
- **This repository is now the home of cramera.** The six-hourly sync from the CRAM
  fork is retired and its workflow removed; development, issues and pull requests
  happen here. Packaging metadata points at this repository.
- **The browser-side tests run here.** The 87 Node.js test files moved from the fork's
  `test/cramera_test/js` to `test/js` with the fixtures they read, and a `Tests`
  workflow runs them on every push and pull request. Five tests that had been failing
  on the fork are fixed.

### Isaac Sim import
- **Convert an Isaac Sim / USD stage into a scene bundle** with `cramera-import` (the
  `usd` extra): geometry, physics joints, OmniPBR, OmniSurface, OmniGlass and
  UsdPreviewSurface materials, lights and `sdt`/`ont` semantics, written into a shared
  `CRAMERA_SCENES` folder. See [Import an Isaac Sim / USD scene](README.md#import-an-isaac-sim--usd-scene).
- **Only the environment comes over**: robots the stage marks (Isaac Sim's
  `IsaacRobotAPI`, or `sdt:entityType` `Robot`) are left out together with the joints
  to them and listed in `conversion_report.json`. `--exclude` leaves out any other
  subtree, `--keep-robots` keeps the marked robots, and `--robot` keeps the one a
  recording replays. A `--prim` inside a left-out robot is refused, and the report and
  the terminal summary name the bodies that hung from a robot (`detached`) and the
  articulations kept in the environment (`articulations`), where a robot part or a
  robot the stage does not mark shows up.
- **Recorded motion plays back**: the stage's time samples or a recorded body trace
  (`--trace`) become joint values, a robot model (`--robot`), moving objects and step
  segments (`--record`), checked against the recording in `residuals.csv`.
- **An imported scene loads as a CRAM world** whose prims answer EQL questions:
  `python -m cramera.examples.usd_scene_world --scene <name> --live`.

### 3D scene
- **A bundle can bring its own lighting and view**: the stage's lights become the
  environment map and key light, the shadows fit the scene's bounds, the plank floor
  can be turned off, and the camera takes a field of view and a far plane.
- Scenes with ambient occlusion on render brighter: the occlusion pass now encodes its
  output for the screen.

## Week 36 · 31 Aug – 4 Sep 2026

### Plan Builder
- **A page that composes a plan and generates a runnable demo.** Drag steps into a
  plan, choose an environment, place the robot and the transport targets in the
  embedded live 3D scene, then generate a `RobotDemonstration` subclass in the
  output style you pick. The demo's run log is shown so a failure can be traced.
  (`205d79402`, `c47b507e1`, `caaa4bb67`, `89a9637a2`, `6261fb2c5`, `e86a79a04`, `3563737a0`)
- **Navigate steps show and capture the robot pose** as a ring and heading arrow on
  the floor. (`487bb1051`, `932644a4f`)
- **Transport steps know where they start and where they drop off**; a drop-off can
  be a semantic surface or container instead of coordinates.
  (`a18b55ebd`, `d522049af`, `6c86d9406`, `1226f653c`, `1890c9ad6`)
- **Objects are placed with sliders, numbers and "Drop to surface"**; new objects
  stage above the robot under a bobbing arrow. (`e3330c9bd`, `316bc29b1`, `92d74ec26`, `38ba4511c`, `520ee76cd`)
- **Every semantic_digital_twin robot and an all-STL industrial object set**; Garmi
  joins the list. (`40edf54b8`, `1827604c3`, `f55097eb4`, `52a548e19`, `18cd18b93`)
- Constraints in plain language on every plan step. (`d53f0aac0`)
- A big 3D scene with the controls in one dropdown; resizable columns; a reload
  button for the scene; visible feedback on Generate, Save and Download.
  (`2af538b25`, `805d8cbb9`, `2186d4121`, `3623aab48`, `ae75551a8`)

### Plan view
- **Plan steps read as a tree of phases with live status**; the graph view is gone,
  the step list is the Plan tab. (`e32c9ddf4`, `874eb01fc`, `0e8da5a51`, `52b2753b5`, `1ae600f4d`)
- **Constraints you type compile to giskardpy nodes against the live world**, from a
  palette inside the Steps view; the bridge accepts them while the plan runs.
  (`c67f5ce5c`, `1ba0b54a4`, `b0918c4eb`)
- The Plan tab attaches to the running bridge and shows motion-statechart rings.
  (`4dd70f65e`, `3e2d609b8`, `359422308`)
- More constraint goal types and a richer rule-based translation to English.
  (`896ccf0c6`, `8c74f6aea`)

### Questions
- **Ask the EQL console a question in English**: completion, matching to a preset,
  read-back and speech. (`b725dfa46`, `8d666e350`)
- **An answer says where it comes from and points at what it names**; a timestamped
  answer can replay its moment. (`62183bd31`, `241f6939e`, `bf25a199a`, `3714d66cc`)
- A query source can claim the bodies it describes under a prefix. (`8b7945244`)

### Recordings
- **Recordings carry the motion statecharts** (`statecharts.json`). (`c1d8fa054`)
- **Recordings carry what the detectors saw**: pick and place events from segmind,
  answerable from the saved scene without the detectors' package.
  (`932f187a6`, `08ec63b2e`, `1ba75a6b2`, `639acac4c`, `e97c698d8`, `479dd4820`)
- **A recording is saved by what it is** — robot, environment, task — and keeps the
  objects a run carried. (`47b156e63`, `3a2c114d4`, `120891514`)

### 3D scene
- The rectangle on the floor of every scene is gone: the SSAO pass no longer draws
  the scene background into its depth and normal buffers. (`993394afc`)
- A lone panel takes the whole window; overlays and question groups fold away.
  (`2eb61a848`, `c861b8299`, `10d050718`, `240f2f5ca`)
- The Teleoperation tab is now called Sandbox; the blue place target stays out of the
  embedded and live views. (`e63e0f917`, `98b1936a6`, `a141673a2`)

### Platform
- The browser no longer caches static assets; what the branch serves is tested. (`12deaee25`)
- A fresh checkout runs the generated demo without editing a venv path. (`f0741687a`)

## Week 34 · 17 – 18 Aug 2026

- Collada models load with their up axis normalized — contributed by Tigul (PR #38).
  (`d6cef3972`)

## Week 33 · 10 – 15 Aug 2026

### Recordings
- Replay a recorded run, with TF frames and keypoints; trim it before saving.
  (`2db5dd0de`, `1bd3a6fea`, `5438761d5`)
- Onboard demos whose worlds come from Gazebo/SDF or MJCF. (`d6d0ee546`, `cc6559743`)

### 3D scene
- ROS debug markers in the scene, with RViz-style settings. (`32fe7d1ff`, `3849c589f`)
- A View menu chooses which panels show and where; panels are draggable and the
  arrangement persists. (`2207d8c51`, `949829ebc`, `1841d1810`, `463991e6f`, `3a20488ea`)
- Loose objects spawned as primitive boxes are recorded and rendered; mesh rendering
  of bundled environments and multi-material robots is fixed. (`2c546d025`, `72bacdd6d`)

### Models
- The Models tab: the probabilistic-model workbench in the browser — Query,
  Posterior and Mode side by side, sliders for numeric constraints.
  (`de6d10091`, `c3b2c3d05`, `95ba8ef7f`, `aa756483f`, `d83816ac1`)

### Live view
- The live view is rebuilt on `WorldVisualization`; no more monkey patches.
  (`33ca53d4a`, `47700539a`, `6d6f543dd`, `f68daa892`)
- Worlds that are not fully XML can be viewed live. (`021c08f68`)

### Platform
- The cramera server opens the viewer; demos only connect to it. (`c1c07a398`)
- Several scenes per installation, selectable across backend and panels. (`4d2d025bb`, `0891832eb`)
- Graph panel: colors and groups, a plan legend, a four-level architecture scan.
  (`0f07103aa`, `1fa024a56`, `41a12b9f7`, `8696eac41`)
- Variable hints in the EQL query box; panels report their errors. (`7264cd6bc`, `ccdeea54a`)
- The project is named cramera. (`2851475ac`)

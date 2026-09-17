/* ============================================================================
 * core/vr-mode.js — viewing the 3D scene from inside it, through a WebXR headset.
 *
 * The desktop viewer looks at the scene through an orbiting camera; a headset
 * instead carries the camera itself, so the camera is parked inside a *rig* —
 * a Group standing on the scene's floor — and the headset's own pose rides on
 * top of it. Moving the viewer through the scene means moving that rig: the
 * camera's local pose belongs to WebXR and is overwritten every frame.
 *
 * The rig is a direct child of the three.js scene, not of the world root: it
 * lives in y-up headset space, while worldRoot carries the z-up->y-up rotation
 * the URDF world needs.
 *
 * Locomotion is teleport plus snap turn, both deliberately discontinuous —
 * gliding a headset viewer through a room is the classic way to make them sick,
 * and the desktop viewer's "follow the robot" camera glide is off in here for
 * the same reason.
 *
 * The page keeps its own DOM UI (layers, pickers, timeline); none of it is
 * visible through a headset, so a session is view-only by design. Everything is
 * set up on the desktop page first, then the headset is put on.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: the WebXR session mode a headset presents
  const SESSION_MODE = 'immersive-vr';
  //: the reference space that puts the origin on the physical floor, so a rig at
  //: y=0 stands on the scene's ground rather than floating at head height
  const FLOOR_SPACE = 'local-floor';
  //: how far a controller's aim ray reaches, in metres
  const RAY_LENGTH = 14;
  //: how far one snap turn rotates the rig
  const SNAP_TURN = Math.PI / 6;
  //: how far the thumbstick has to go before it counts as a turn
  const SNAP_DEADZONE = 0.72;
  //: how far back it has to come before the next turn is armed
  const SNAP_RELEASE = 0.35;
  //: the teleport reticle's radius, in metres
  const RETICLE_RADIUS = 0.28;
  //: colours for an aim ray that has a landing spot and one that does not
  const AIM_OK = 0x49c2ff;
  const AIM_BAD = 0x555f6e;
  //: url flag that drops shadows for the session — the first thing to try when a
  //: scene renders fine on the desktop but misses frame rate in stereo
  const NO_SHADOW_FLAG = /[?&]vr=noshadow(&|$)/;
  //: where a session starts, as [x, y] on the world floor.
  //:
  //: These are the *world* frame's coordinates — the z-up frame the URDF world,
  //: the published poses and the markers all use — not three's y-up space, so this
  //: reads the same way as any other pose in the scene. worldRoot converts.
  const ENTRY_XY = [0, 3];

  //: how often at most the headset tells the live bridge where it is, in ms
  const POST_INTERVAL_MS = 100;
  //: how far the head must move, in metres, before that is worth a post
  const POST_MOVE = 0.02;
  //: how far it must turn, in radians, before that is worth a post
  const POST_TURN = 2 * Math.PI / 180;

  const UP = new THREE.Vector3(0, 1, 0);
  //: Turns a headset-space pose into a marker pose.
  //:
  //: A head and a controller both look down their own -Z, while the marker shapes a
  //: viewer is drawn with are laid out along their pose's +X (see
  //: ``cramera.live.avatar.PART_SHAPES``). One quarter turn about the vertical
  //: reconciles them, and keeps the roll the headset reported, which aiming the
  //: axis alone would lose.
  const FORWARD_TO_X = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);

  //: An identifier for this page's viewer, stable for as long as the tab is open.
  //:
  //: The bridge keys one avatar per viewer off this, so two headsets on the same
  //: demo each get their own arrow rather than fighting over one.
  const VIEWER_ID = 'vr-' + Math.random().toString(36).slice(2, 10);

  //: Whether this browser can present to a headset at all.
  //:
  //: WebXR needs a secure context, so a page served over plain http to anything
  //: but localhost has no navigator.xr and answers false here.
  //:
  //: :param cb: Called with true or false once the browser has answered.
  function supported(cb) {
    if (!global.navigator || !global.navigator.xr || !global.navigator.xr.isSessionSupported) {
      cb(false);
      return;
    }
    global.navigator.xr.isSessionSupported(SESSION_MODE)
      .then(function (ok) { cb(!!ok); })
      .catch(function () { cb(false); });
  }

  //: The aim ray one controller draws, as a line down its own -Z.
  function aimRay() {
    const geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, -1),
    ]);
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({
      color: AIM_BAD, transparent: true, opacity: 0.85,
    }));
    line.name = 'vr-aim';
    line.scale.z = RAY_LENGTH;
    return line;
  }

  //: The ring that marks where a teleport would land.
  function reticle() {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(RETICLE_RADIUS * 0.62, RETICLE_RADIUS, 32),
      new THREE.MeshBasicMaterial({ color: AIM_OK, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthTest: false })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.renderOrder = 10;
    ring.visible = false;
    return ring;
  }

  //: A small block standing in for the controller in the user's hand.
  function handBlock() {
    return new THREE.Mesh(
      new THREE.BoxGeometry(0.045, 0.035, 0.12),
      new THREE.MeshStandardMaterial({ color: 0x2a3340, roughness: 0.6, metalness: 0.1 })
    );
  }

  //: Install VR mode on a mounted 3D scene.
  //:
  //: :param options: ``renderer``, ``scene``, ``camera``, ``controls`` (the
  //:     OrbitControls to stand down while presenting), ``ground`` (the mesh a
  //:     teleport ray lands on), ``worldRoot`` (the z-up world group, which turns
  //:     :data:`ENTRY_XY` into a spot in the headset's y-up space), ``button``
  //:     (the element that opens a session) and ``onChange`` (called with true on
  //:     entering and false on leaving).
  //: :return: The mode's handle — ``presenting()``, ``update()`` to be called
  //:     once per frame while presenting, and ``stats()`` for the last session's
  //:     frame timing.
  function install(options) {
    const renderer = options.renderer;
    const scene = options.scene;
    const camera = options.camera;
    const controls = options.controls;
    const ground = options.ground;
    const worldRoot = options.worldRoot;
    const live = options.live;
    const button = options.button;
    const onChange = options.onChange || function () {};

    // the camera stops being a loose object and becomes the rig's passenger; while
    // no session runs the rig sits at the origin, so the desktop camera is
    // unaffected — its local pose is still its world pose
    const rig = new THREE.Group();
    rig.name = 'vr-rig';
    rig.add(camera);
    scene.add(rig);

    const raycaster = new THREE.Raycaster();
    const mark = reticle();
    scene.add(mark);

    const head = new THREE.Vector3();
    const origin = new THREE.Vector3();
    const direction = new THREE.Vector3();
    const offset = new THREE.Vector3();
    const entry = new THREE.Vector3();
    const centre = new THREE.Vector3();
    const facing = new THREE.Quaternion();
    // the avatar's poses, built in the world's z-up frame
    const worldFromScene = new THREE.Matrix4();
    const sceneToWorld = new THREE.Quaternion();
    const partPosition = new THREE.Vector3();
    const partQuaternion = new THREE.Quaternion();

    let session = null;
    let landing = null;          // the world point the active aim would teleport to
    let aimingWith = null;       // the controller holding the teleport button
    let turnArmed = true;        // false while the thumbstick is still pushed over
    let shadowsWere = null;      // renderer.shadowMap.enabled before the session
    let frames = 0, elapsed = 0, worst = 0, lastStats = null;
    let postedAt = 0;            // when the avatar last went to the bridge
    let postedParts = null;      // part name -> the pose last posted, for the deadband
    const grips = [];            // the controllers' grip spaces, by input-source index

    const controllers = [0, 1].map(function (index) {
      const controller = renderer.xr.getController(index);
      controller.add(aimRay());
      controller.addEventListener('selectstart', function () { aimingWith = controller; });
      controller.addEventListener('selectend', function () {
        if (aimingWith === controller && landing) teleport(landing);
        aimingWith = null;
        landing = null;
        mark.visible = false;
      });
      rig.add(controller);

      const grip = renderer.xr.getControllerGrip(index);
      grip.add(handBlock());
      rig.add(grip);
      grips[index] = grip;
      return controller;
    });

    //: Stand the rig on :data:`ENTRY_XY`, facing away from the world origin.
    //:
    //: A session always starts from the same spot rather than from wherever the
    //: orbit camera happened to be: that camera can sit below the floor or 20 m
    //: out, neither of which is somewhere to put a person.
    function placeRig() {
      if (worldRoot) worldRoot.updateWorldMatrix(true, false);
      entry.set(ENTRY_XY[0], ENTRY_XY[1], 0);
      centre.set(0, 0, 0);
      if (worldRoot) {
        entry.applyMatrix4(worldRoot.matrixWorld);
        centre.applyMatrix4(worldRoot.matrixWorld);
      }
      rig.position.set(entry.x, 0, entry.z);
      offset.subVectors(centre, entry);
      offset.y = 0;
      if (offset.lengthSq() < 1e-6) offset.set(0, 0, 1);
      rig.rotation.set(0, Math.atan2(offset.x, offset.z), 0);
    }

    //: Move the rig so the viewer's *head* lands on ``point``.
    //:
    //: With a floor-relative reference space the head is wherever the user has
    //: physically walked to inside their play space, which is not above the rig's
    //: origin; teleporting the rig itself would land them that same offset away
    //: from where they aimed.
    function teleport(point) {
      camera.getWorldPosition(head);
      rig.position.x += point.x - head.x;
      rig.position.z += point.z - head.z;
      rig.position.y = 0;
    }

    //: Rotate the rig by ``angle`` about the vertical axis through the head, so a
    //: turn pivots around the viewer instead of swinging them around the origin.
    function snapTurn(angle) {
      camera.getWorldPosition(head);
      rig.position.sub(head).applyAxisAngle(UP, angle).add(head);
      rig.rotation.y += angle;
    }

    //: Follow the aiming controller's ray to the floor and move the reticle there.
    function aim() {
      const controller = aimingWith;
      if (!controller) {
        mark.visible = false;
        landing = null;
        controllers.forEach(function (c) {
          const idle = c.getObjectByName('vr-aim');
          if (idle) idle.material.color.setHex(AIM_BAD);
        });
        return;
      }
      const ray = controller.getObjectByName('vr-aim');
      controller.getWorldPosition(origin);
      direction.set(0, 0, -1).applyQuaternion(controller.getWorldQuaternion(facing));
      raycaster.set(origin, direction);
      raycaster.far = RAY_LENGTH;
      const hit = ground ? raycaster.intersectObject(ground, false)[0] : null;
      landing = hit ? hit.point.clone() : null;
      mark.visible = !!landing;
      if (landing) mark.position.set(landing.x, landing.y + 0.01, landing.z);
      if (ray) ray.material.color.setHex(landing ? AIM_OK : AIM_BAD);
    }

    //: Read the thumbsticks and snap-turn on a fresh push past the deadzone.
    function readTurn() {
      if (!session) return;
      let push = 0;
      const sources = session.inputSources;
      for (let i = 0; i < sources.length; i++) {
        const pad = sources[i].gamepad;
        if (!pad || !pad.axes) continue;
        // thumbstick first (axes 2/3 on a standard xr-controller), touchpad second
        const x = pad.axes.length > 2 ? pad.axes[2] : pad.axes[0];
        if (typeof x === 'number' && Math.abs(x) > Math.abs(push)) push = x;
      }
      if (turnArmed && Math.abs(push) > SNAP_DEADZONE) {
        snapTurn(push > 0 ? -SNAP_TURN : SNAP_TURN);
        turnArmed = false;
      } else if (!turnArmed && Math.abs(push) < SNAP_RELEASE) {
        turnArmed = true;
      }
    }

    //: Send one body to the live bridge's avatar route, ignoring the outcome.
    //:
    //: A demo can end at any moment and the bridge goes with it; a headset losing
    //: its avatar is not a reason to interrupt the person wearing it.
    //:
    //: :param base: The bridge's base url.
    //: :param body: The JSON body to post.
    //: :param leaving: Whether the page may be going away as this is sent.
    function post(base, body, leaving) {
      try {
        global.fetch(base + '/avatar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          keepalive: !!leaving,
        }).catch(function () {});
      } catch (e) { /* the bridge is optional */ }
    }

    //: Publish where this headset is, as a marker in the running demo's world.
    //:
    //: Only while a demo is live: ``live()`` hands back the bridge's url or null,
    //: and without a bridge there is nothing to join.
    //:
    //: The rate limit and deadband are not politeness. A changed marker set makes
    //: every viewer refetch /markers and rebuild the whole overlay from scratch, so
    //: a head reported raw would have every desktop page tearing down and rebuilding
    //: every collision and costmap marker in the scene at the poll rate.
    function publishAvatar() {
      const base = live && live();
      if (!base) { postedParts = null; return; }
      const now = (global.performance && global.performance.now()) || Date.now();
      if (now - postedAt < POST_INTERVAL_MS) return;

      // three's y-up world back into the z-up frame every published pose uses
      if (worldRoot) {
        worldRoot.updateWorldMatrix(true, false);
        worldFromScene.copy(worldRoot.matrixWorld).invert();
        worldRoot.getWorldQuaternion(sceneToWorld).invert();
      } else {
        worldFromScene.identity();
        sceneToWorld.identity();
      }

      const parts = [{ name: 'head', object: camera }];
      // handedness comes off the input source, not the slot: which controller is
      // index 0 is the headset's business and can differ between runs
      const sources = (session && session.inputSources) || [];
      for (let i = 0; i < sources.length; i++) {
        const hand = sources[i] && sources[i].handedness;
        const grip = grips[i];
        // an untracked grip has no pose to report — a controller put down or asleep
        if (!grip || !grip.visible) continue;
        if (hand === 'left' || hand === 'right') parts.push({ name: hand, object: grip });
      }

      const measured = [];
      let changed = !postedParts || postedParts.length !== parts.length;
      for (let i = 0; i < parts.length; i++) {
        const object = parts[i].object;
        object.getWorldPosition(partPosition).applyMatrix4(worldFromScene);
        partQuaternion.copy(sceneToWorld)
          .multiply(object.getWorldQuaternion(facing))
          .multiply(FORWARD_TO_X);
        const pose = {
          name: parts[i].name,
          position: partPosition.clone(),
          quaternion: partQuaternion.clone(),
        };
        measured.push(pose);
        if (changed) continue;
        const was = postedParts[i];
        if (was.name !== pose.name
          || was.position.distanceTo(pose.position) >= POST_MOVE
          || was.quaternion.angleTo(pose.quaternion) >= POST_TURN) changed = true;
      }
      if (!changed) return;

      postedAt = now;
      postedParts = measured;
      post(base, {
        viewer: VIEWER_ID,
        parts: measured.map(function (pose) {
          return {
            name: pose.name,
            position: [pose.position.x, pose.position.y, pose.position.z],
            quaternion: [
              pose.quaternion.x, pose.quaternion.y, pose.quaternion.z, pose.quaternion.w,
            ],
          };
        }),
      }, false);
    }

    //: Take this headset's avatar back out of the scene.
    //:
    //: :param leaving: Whether the page itself is going away.
    function withdrawAvatar(leaving) {
      const base = live && live();
      postedParts = null;
      if (base) post(base, { viewer: VIEWER_ID, gone: true }, leaving);
    }

    //: One frame of VR mode: aim, turn, publish the avatar, and keep the frame-time
    //: tally.
    //:
    //: :param delta: Seconds since the previous frame.
    function update(delta) {
      aim();
      readTurn();
      publishAvatar();
      if (delta > 0 && delta < 1) {
        frames++;
        elapsed += delta;
        if (delta > worst) worst = delta;
      }
    }

    function onSessionStart() {
      session = renderer.xr.getSession();
      frames = 0; elapsed = 0; worst = 0;
      controls.enabled = false;
      placeRig();
      if (NO_SHADOW_FLAG.test(global.location.search)) {
        shadowsWere = renderer.shadowMap.enabled;
        renderer.shadowMap.enabled = false;
        // a shadow-map change only reaches materials that recompile
        scene.traverse(function (object) {
          if (object.material) [].concat(object.material).forEach(function (m) { m.needsUpdate = true; });
        });
      }
      button.textContent = '🥽 Exit VR';
      onChange(true);
    }

    function onSessionEnd() {
      lastStats = frames > 1
        ? { fps: frames / elapsed, worstMs: worst * 1000, frames: frames }
        : null;
      withdrawAvatar(false);
      session = null;
      aimingWith = null;
      landing = null;
      mark.visible = false;
      rig.position.set(0, 0, 0);
      rig.rotation.set(0, 0, 0);
      if (shadowsWere !== null) {
        renderer.shadowMap.enabled = shadowsWere;
        scene.traverse(function (object) {
          if (object.material) [].concat(object.material).forEach(function (m) { m.needsUpdate = true; });
        });
        shadowsWere = null;
      }
      controls.enabled = true;
      button.textContent = '🥽 Enter VR';
      onChange(false);
    }

    renderer.xr.addEventListener('sessionstart', onSessionStart);
    renderer.xr.addEventListener('sessionend', onSessionEnd);
    // a headset whose tab is closed mid-session never reaches sessionend; the
    // bridge sweeps an avatar that goes quiet, but saying so is quicker
    global.addEventListener('pagehide', function () {
      if (session) withdrawAvatar(true);
    });

    function enter() {
      if (session) { session.end(); return; }
      renderer.xr.setReferenceSpaceType(FLOOR_SPACE);
      global.navigator.xr.requestSession(SESSION_MODE, {
        optionalFeatures: [FLOOR_SPACE, 'bounded-floor'],
      }).then(function (granted) {
        return renderer.xr.setSession(granted);
      }).catch(function (e) {
        button.title = 'Could not start a VR session: ' + e;
      });
    }

    // Harmless without a headset — the renderer only consults this once a session
    // presents — and set up front so it is never read before the support probe
    // below has answered.
    renderer.xr.enabled = true;

    button.addEventListener('click', enter);
    supported(function (ok) {
      button.style.display = ok ? '' : 'none';
      button.title = ok
        ? 'View the scene from inside it. Trigger to teleport, thumbstick to turn.'
        : '';
    });

    return {
      presenting: function () { return !!session; },
      update: update,
      stats: function () { return lastStats; },
      rig: rig,
    };
  }

  global.VRMode = {
    SESSION_MODE: SESSION_MODE,
    supported: supported,
    install: install,
  };
})(window);

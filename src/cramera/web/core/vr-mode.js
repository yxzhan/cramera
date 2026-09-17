/* ============================================================================
 * core/vr-mode.js — viewing the 3D scene from inside it, through a WebXR headset.
 *
 * The camera rides in the rig core/viewer-rig.js builds, and under a session its
 * own local pose belongs to WebXR: moving a headset viewer through the scene means
 * moving that rig. What this module adds on top is the session itself, the
 * controllers, and the two moves a headset makes — teleport and snap turn, both
 * deliberately discontinuous, since gliding a headset viewer through a room is the
 * classic way to make them sick.
 *
 * The page keeps its own DOM UI (layers, pickers, timeline); none of it is visible
 * through a headset, so a session is view-only by design. Everything is set up on
 * the desktop page first, then the headset is put on.
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
  //: :param options: ``renderer``, ``scene``, ``camera``, ``rig`` (the ViewerRig
  //:     handle), ``controls`` (the OrbitControls to stand down while presenting),
  //:     ``ground`` (the mesh a teleport ray lands on), ``button`` (the element
  //:     that opens a session) and ``onChange`` (called with true on entering and
  //:     false on leaving).
  //: :return: The mode's handle — ``presenting()``, ``update()`` to be called once
  //:     per frame while presenting, ``hands()`` for the parts a session reports,
  //:     and ``stats()`` for the last session's frame timing.
  function install(options) {
    const renderer = options.renderer;
    const scene = options.scene;
    const camera = options.camera;
    const rig = options.rig;
    const controls = options.controls;
    const ground = options.ground;
    const button = options.button;
    const onChange = options.onChange || function () {};

    const raycaster = new THREE.Raycaster();
    const mark = reticle();
    scene.add(mark);

    const origin = new THREE.Vector3();
    const direction = new THREE.Vector3();
    const facing = new THREE.Quaternion();

    let session = null;
    let landing = null;          // the world point the active aim would teleport to
    let aimingWith = null;       // the controller holding the teleport button
    let turnArmed = true;        // false while the thumbstick is still pushed over
    let shadowsWere = null;      // renderer.shadowMap.enabled before the session
    let frames = 0, elapsed = 0, worst = 0, lastStats = null;
    const grips = [];            // the controllers' grip spaces, by input-source index

    const controllers = [0, 1].map(function (index) {
      const controller = renderer.xr.getController(index);
      controller.add(aimRay());
      controller.addEventListener('selectstart', function () { aimingWith = controller; });
      controller.addEventListener('selectend', function () {
        if (aimingWith === controller && landing) rig.moveTo(landing);
        aimingWith = null;
        landing = null;
        mark.visible = false;
      });
      rig.group.add(controller);

      const grip = renderer.xr.getControllerGrip(index);
      grip.add(handBlock());
      rig.group.add(grip);
      grips[index] = grip;
      return controller;
    });

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
        rig.turn(push > 0 ? -SNAP_TURN : SNAP_TURN);
        turnArmed = false;
      } else if (!turnArmed && Math.abs(push) < SNAP_RELEASE) {
        turnArmed = true;
      }
    }

    //: The parts a session is tracking beyond the head, for the avatar.
    //:
    //: An untracked grip has no pose to report — a controller put down or asleep.
    //: Handedness comes off the input source, not the slot: which controller is
    //: index 0 is the headset's business and can differ between runs.
    function hands() {
      const parts = [];
      const sources = (session && session.inputSources) || [];
      for (let i = 0; i < sources.length; i++) {
        const hand = sources[i] && sources[i].handedness;
        const grip = grips[i];
        if (!grip || !grip.visible) continue;
        if (hand === 'left' || hand === 'right') parts.push({ name: hand, object: grip });
      }
      return parts;
    }

    //: One frame of VR mode: aim, turn, and keep the frame-time tally.
    //:
    //: :param delta: Seconds since the previous frame.
    function update(delta) {
      aim();
      readTurn();
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
      // a headset reports its own head height, so the camera sits at the rig's own
      // origin and its local pose is WebXR's from here on; only the rig is ours
      rig.adopt(0);
      rig.place();
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
      session = null;
      aimingWith = null;
      landing = null;
      mark.visible = false;
      rig.release();
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
      hands: hands,
      stats: function () { return lastStats; },
    };
  }

  global.VRMode = {
    SESSION_MODE: SESSION_MODE,
    supported: supported,
    install: install,
  };
})(window);

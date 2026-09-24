/* ============================================================================
 * core/fps-mode.js — walking through the scene, on a desktop or a phone.
 *
 * The same first-person body either way: the rig stands on the floor and carries
 * the heading, the camera sits at eye height and carries the pitch, and movement is
 * along the heading with the pitch ignored, so looking up does not lift the viewer
 * off the ground. Only where the two numbers come from differs — a keyboard and the
 * mouse, or a thumb on each half of the screen.
 *
 * Nothing here needs WebXR, a headset or a secure context: a phone reaches it by
 * opening the same link. A walking viewer is published into a live demo exactly as
 * a headset viewer is, minus the hands, so the two see each other.
 *
 * The mouse is read through pointer lock where the browser grants it, and by
 * dragging where it does not, so the mode still works inside an iframe or wherever
 * a lock is refused.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: how fast a viewer walks, in metres per second
  const WALK_SPEED = 2.4;
  //: the multiplier while the run key is held
  const RUN_MULTIPLIER = 2.4;
  //: radians of turn per pixel of mouse movement under pointer lock
  const MOUSE_SENSITIVITY = 0.0022;
  //: radians of turn per pixel of drag, for a thumb or an unlocked mouse
  const DRAG_SENSITIVITY = 0.005;
  //: how far the view may tip up or down, in radians
  const PITCH_LIMIT = 85 * Math.PI / 180;
  //: the on-screen stick's travel, in pixels, from centre to full deflection
  const STICK_RANGE = 58;
  //: class on the root element while a viewer is walking; app.css hands the canvas
  //: every drag and gesture, so a thumb steers instead of scrolling the page
  const WALKING_CLASS = 'walking';

  //: which keys drive which direction, as [forward, right] contributions
  const KEY_MOVES = {
    KeyW: [1, 0], ArrowUp: [1, 0],
    KeyS: [-1, 0], ArrowDown: [-1, 0],
    KeyA: [0, -1], ArrowLeft: [0, -1],
    KeyD: [0, 1], ArrowRight: [0, 1],
  };
  //: the key that makes a viewer run
  const RUN_KEYS = { ShiftLeft: true, ShiftRight: true };

  //: Whether this browser is being driven by touch.
  //:
  //: Decides which half of the help text to show and whether to reach for pointer
  //: lock, nothing more — the stick appears when a thumb lands on the screen, so a
  //: device with both stays usable either way.
  function touchFirst() {
    return global.matchMedia && global.matchMedia('(pointer: coarse)').matches;
  }

  //: The on-screen stick, hidden until a thumb puts it somewhere.
  function stickElement() {
    const root = document.createElement('div');
    root.className = 'fps-stick';
    root.hidden = true;
    const knob = document.createElement('div');
    knob.className = 'fps-stick-knob';
    root.appendChild(knob);
    return { root: root, knob: knob };
  }

  //: Install walking on a mounted 3D scene.
  //:
  //: :param options: ``renderer``, ``camera``, ``rig`` (the ViewerRig handle),
  //:     ``controls`` (the OrbitControls to stand down while walking),
  //:     ``container`` (the element the stick is drawn over), ``button``,
  //:     ``hint`` (an element to write the controls into, optional) and
  //:     ``onChange`` (called with true on entering and false on leaving).
  //: :return: ``active()``, and ``update(delta)`` to be called once per frame,
  //:     which reports whether the view moved.
  function install(options) {
    const renderer = options.renderer;
    const camera = options.camera;
    const rig = options.rig;
    const controls = options.controls;
    const container = options.container;
    const button = options.button;
    const hint = options.hint;
    const onChange = options.onChange || function () {};

    const canvas = renderer.domElement;
    const stick = stickElement();
    container.appendChild(stick.root);

    let active = false;
    let pitch = 0;
    let running = false;
    const held = {};                 // key code -> true while down
    let stickPointer = null;         // pointerId steering, and where it started
    let stickOrigin = { x: 0, y: 0 };
    let stickVector = { forward: 0, right: 0 };
    let lookPointer = null;          // pointerId looking, and where it last was
    let lookFrom = { x: 0, y: 0 };
    const move = new THREE.Vector3();

    //: Apply a turn, in radians, from whichever input produced it.
    function look(deltaYaw, deltaPitch) {
      rig.group.rotation.y -= deltaYaw;
      pitch = Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, pitch - deltaPitch));
      camera.rotation.set(pitch, 0, 0);
    }

    // %% keyboard
    function onKeyDown(event) {
      if (!active) return;
      if (RUN_KEYS[event.code]) running = true;
      if (!KEY_MOVES[event.code]) return;
      held[event.code] = true;
      event.preventDefault();          // arrows would otherwise scroll the page
    }
    function onKeyUp(event) {
      if (RUN_KEYS[event.code]) running = false;
      delete held[event.code];
    }

    // %% pointer: a thumb on the left half steers, anywhere else looks
    function onPointerDown(event) {
      if (!active) return;
      const rect = canvas.getBoundingClientRect();
      const steering = event.pointerType === 'touch'
        && event.clientX - rect.left < rect.width / 2;
      if (steering && stickPointer === null) {
        stickPointer = event.pointerId;
        stickOrigin = { x: event.clientX, y: event.clientY };
        stick.root.style.left = (event.clientX - rect.left) + 'px';
        stick.root.style.top = (event.clientY - rect.top) + 'px';
        stick.knob.style.transform = 'translate(-50%, -50%)';
        stick.root.hidden = false;
      } else if (lookPointer === null) {
        lookPointer = event.pointerId;
        lookFrom = { x: event.clientX, y: event.clientY };
        // a locked pointer reports deltas instead, and hides the cursor
        if (event.pointerType === 'mouse' && canvas.requestPointerLock
          && document.pointerLockElement !== canvas) canvas.requestPointerLock();
      }
      // Not under pointer lock: a locked pointer cannot be captured -- the lock already
      // routes it here -- and asking throws InvalidStateError. Chrome grants the lock
      // requested just above quickly enough to be in place by now on the first click.
      if (document.pointerLockElement !== canvas) {
        try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* locked meanwhile */ }
      }
      event.preventDefault();
    }

    function onPointerMove(event) {
      if (!active) return;
      if (document.pointerLockElement === canvas) {
        look(event.movementX * MOUSE_SENSITIVITY, event.movementY * MOUSE_SENSITIVITY);
        return;
      }
      if (event.pointerId === stickPointer) {
        const dx = (event.clientX - stickOrigin.x) / STICK_RANGE;
        const dy = (event.clientY - stickOrigin.y) / STICK_RANGE;
        const length = Math.sqrt(dx * dx + dy * dy);
        const clamped = length > 1 ? 1 / length : 1;
        stickVector = { forward: -dy * clamped, right: dx * clamped };
        stick.knob.style.transform = 'translate(-50%, -50%) translate('
          + (dx * clamped * STICK_RANGE) + 'px,' + (dy * clamped * STICK_RANGE) + 'px)';
        return;
      }
      if (event.pointerId !== lookPointer) return;
      look((event.clientX - lookFrom.x) * DRAG_SENSITIVITY,
        (event.clientY - lookFrom.y) * DRAG_SENSITIVITY);
      lookFrom = { x: event.clientX, y: event.clientY };
    }

    function onPointerUp(event) {
      if (event.pointerId === stickPointer) {
        stickPointer = null;
        stickVector = { forward: 0, right: 0 };
        stick.root.hidden = true;
      }
      if (event.pointerId === lookPointer) lookPointer = null;
    }

    // %% the walk itself
    //: One frame of walking.
    //:
    //: :param delta: Seconds since the previous frame.
    //: :return: Whether the view moved and so has to be redrawn.
    function update(delta) {
      if (!active) return false;
      let forward = stickVector.forward;
      let right = stickVector.right;
      for (const code in held) {
        forward += KEY_MOVES[code][0];
        right += KEY_MOVES[code][1];
      }
      if (!forward && !right) return false;
      move.set(right, 0, -forward);
      if (move.lengthSq() > 1) move.normalize();
      // along the heading only: looking up must not lift the viewer off the floor
      move.applyAxisAngle(new THREE.Vector3(0, 1, 0), rig.group.rotation.y);
      const speed = WALK_SPEED * (running ? RUN_MULTIPLIER : 1) * Math.min(delta, 0.1);
      rig.group.position.addScaledVector(move, speed);
      rig.group.position.y = rig.floor();
      return true;
    }

    // %% entering and leaving
    function enter() {
      if (active) return;
      active = true;
      controls.enabled = false;
      rig.adopt(ViewerRig.EYE_HEIGHT);
      rig.place();
      pitch = 0;
      document.documentElement.classList.add(WALKING_CLASS);
      button.textContent = '✕ Leave';
      if (hint) {
        hint.textContent = touchFirst()
          ? 'left thumb to walk · drag to look'
          : 'WASD to walk · shift to run · drag or click to look · esc to release the cursor';
      }
      onChange(true);
    }

    function leave() {
      if (!active) return;
      active = false;
      for (const code in held) delete held[code];
      running = false;
      stickPointer = null;
      lookPointer = null;
      stickVector = { forward: 0, right: 0 };
      stick.root.hidden = true;
      document.documentElement.classList.remove(WALKING_CLASS);
      if (document.pointerLockElement === canvas) document.exitPointerLock();
      rig.release();
      controls.enabled = true;
      button.textContent = '🚶 Walk in';
      if (hint) hint.textContent = '';
      onChange(false);
    }

    button.addEventListener('click', function () { if (active) leave(); else enter(); });
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
    global.addEventListener('keydown', onKeyDown);
    global.addEventListener('keyup', onKeyUp);

    return {
      active: function () { return active; },
      update: update,
      leave: leave,
    };
  }

  global.FpsMode = {
    install: install,
    touchFirst: touchFirst,
  };
})(window);

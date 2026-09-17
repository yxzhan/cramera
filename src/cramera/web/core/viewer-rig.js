/* ============================================================================
 * core/viewer-rig.js — the body a viewer stands in, when they are in the scene
 * rather than looking at it.
 *
 * The desktop viewer looks at the scene through an orbiting camera. A viewer who
 * is *inside* it — wearing a headset, or walking around with WASD — carries the
 * camera instead, so the camera is parked in a rig: a Group standing on the
 * scene's floor, whose position and yaw are what moving through the scene means.
 *
 * Who writes the camera's own local pose is what separates the two modes that use
 * this. Under WebXR it belongs to the headset and is overwritten every frame, so
 * the rig is the only thing a session may move. Walking, nothing else writes it,
 * so the camera sits at eye height and carries the pitch while the rig carries the
 * yaw.
 *
 * The rig is a direct child of the three.js scene, not of the world root: it lives
 * in y-up space, while worldRoot carries the z-up->y-up rotation the URDF world
 * needs. Anything expressed in world coordinates passes through worldRoot's matrix.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: where a viewer enters the scene, as [x, y] on the world floor.
  //:
  //: These are the *world* frame's coordinates — the z-up frame the URDF world, the
  //: published poses and the markers all use — not three's y-up space, so this reads
  //: the same way as any other pose in the scene. worldRoot converts.
  const ENTRY_XY = [0, 3];

  //: how far above the floor a viewer's eyes are, in metres, when nothing else says.
  //:
  //: A headset reports its own height and ignores this; walking has to assume one.
  const EYE_HEIGHT = 1.6;

  const UP = new THREE.Vector3(0, 1, 0);

  //: Build the rig and adopt the camera into it.
  //:
  //: :param options: ``scene``, ``camera`` and ``worldRoot``.
  //: :return: The rig's handle — its ``group``, and the moves a mode can make.
  function install(options) {
    const scene = options.scene;
    const camera = options.camera;
    const worldRoot = options.worldRoot;

    // while no mode is active the rig sits at the origin, so the orbiting camera is
    // unaffected: its local pose is still its world pose
    const group = new THREE.Group();
    group.name = 'viewer-rig';
    group.add(camera);
    scene.add(group);

    const head = new THREE.Vector3();
    const entry = new THREE.Vector3();
    const centre = new THREE.Vector3();
    const offset = new THREE.Vector3();
    // where the orbiting view had the camera before a mode took it over
    const parked = { position: new THREE.Vector3(), quaternion: new THREE.Quaternion() };

    //: Take the camera over, remembering where the orbiting view had it.
    //:
    //: From here the camera's local pose is the mode's to write — an offset inside
    //: the rig, no longer a position in the scene — so anything that moves the
    //: orbiting camera has to stand down until :func:`release`.
    //:
    //: :param eyeHeight: How far up the rig to seat the camera; zero for a headset,
    //:     which reports its own head height.
    function adopt(eyeHeight) {
      parked.position.copy(camera.position);
      parked.quaternion.copy(camera.quaternion);
      camera.position.set(0, eyeHeight || 0, 0);
      camera.rotation.set(0, 0, 0);
    }

    //: Stand the rig on :data:`ENTRY_XY`, facing away from the world origin.
    //:
    //: A viewer always enters at the same spot rather than wherever the orbiting
    //: camera happened to be: that camera can sit below the floor or 20 m out,
    //: neither of which is somewhere to put a person.
    function place() {
      if (worldRoot) worldRoot.updateWorldMatrix(true, false);
      entry.set(ENTRY_XY[0], ENTRY_XY[1], 0);
      centre.set(0, 0, 0);
      if (worldRoot) {
        entry.applyMatrix4(worldRoot.matrixWorld);
        centre.applyMatrix4(worldRoot.matrixWorld);
      }
      group.position.set(entry.x, 0, entry.z);
      offset.subVectors(centre, entry);
      offset.y = 0;
      if (offset.lengthSq() < 1e-6) offset.set(0, 0, 1);
      group.rotation.set(0, Math.atan2(offset.x, offset.z), 0);
    }

    //: Move the rig so the viewer's *head* lands on ``point``.
    //:
    //: A headset's head is wherever the wearer has physically walked to inside their
    //: play space, which is not above the rig's origin; moving the rig itself would
    //: land them that same offset away from where they aimed.
    //:
    //: :param point: The destination in three's y-up space.
    function moveTo(point) {
      camera.getWorldPosition(head);
      group.position.x += point.x - head.x;
      group.position.z += point.z - head.z;
      group.position.y = 0;
    }

    //: Rotate the rig about the vertical axis through the head, so a turn pivots
    //: around the viewer rather than swinging them around the rig's origin.
    //:
    //: :param angle: How far to turn, in radians.
    function turn(angle) {
      camera.getWorldPosition(head);
      group.position.sub(head).applyAxisAngle(UP, angle).add(head);
      group.rotation.y += angle;
    }

    //: Hand the camera back, on the view it was orbiting from before.
    //:
    //: Leaving it wherever walking ended would strand the orbiting camera at the
    //: rig's origin, so the next orbit would swing from somewhere nobody chose.
    function release() {
      group.position.set(0, 0, 0);
      group.rotation.set(0, 0, 0);
      camera.position.copy(parked.position);
      camera.quaternion.copy(parked.quaternion);
    }

    return {
      group: group,
      place: place,
      adopt: adopt,
      moveTo: moveTo,
      turn: turn,
      release: release,
    };
  }

  global.ViewerRig = {
    ENTRY_XY: ENTRY_XY,
    EYE_HEIGHT: EYE_HEIGHT,
    install: install,
  };
})(window);

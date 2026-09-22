/* ============================================================================
 * core/viewer-presence.js — telling the running demo where this viewer is.
 *
 * Someone inside the scene is published to it the way every other CRAM component
 * announces itself: as markers, which every other viewer already renders. The
 * bridge turns the poses posted here into an avatar (see cramera.live.avatar); a
 * viewer wearing a headset reports its head and both hands, one walking reports
 * only a head.
 *
 * Only while a demo is live — an avatar is a marker in the running world, so there
 * has to be a running world. Poses go in the *world* z-up frame, the one every
 * other published pose uses, which is what worldRoot converts between.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: how often at most a viewer reports that it has moved, in ms
  const MOVE_INTERVAL_MS = 100;
  //: how far a part must move, in metres, before that is worth a post
  const MOVE_THRESHOLD = 0.02;
  //: how far it must turn, in radians, before that is worth a post
  const TURN_THRESHOLD = 2 * Math.PI / 180;
  //: how often a viewer who has not moved reports in anyway, in ms.
  //:
  //: The thresholds above suppress pose updates, not the viewer itself: the bridge
  //: drops an avatar that has gone quiet (``avatar.SILENCE_TIMEOUT_SECONDS``), so
  //: somebody standing still would otherwise be swept away as though they had left.
  //: Well under that timeout, and rare enough to cost nothing.
  const HEARTBEAT_MS = 1000;
  //: the marker namespace the bridge draws avatars in (``avatar.AVATAR_NAMESPACE``)
  const AVATAR_NAMESPACE = 'vr_viewer';

  //: Turns a viewer-space pose into a marker pose.
  //:
  //: A head, a controller and a walking camera all look down their own -Z, while
  //: the marker shapes an avatar is drawn with are laid out along their pose's +X
  //: (see ``cramera.live.avatar.PART_SHAPES``). One quarter turn about the vertical
  //: reconciles them, and keeps the roll that was reported, which aiming the axis
  //: alone would lose.
  const FORWARD_TO_X = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);

  //: An identifier for this page's viewer, stable for as long as the tab is open.
  //:
  //: The bridge keys one avatar off this, so two people on the same demo each get
  //: their own rather than fighting over one.
  const VIEWER_ID = 'viewer-' + (global.crypto && global.crypto.randomUUID
    ? global.crypto.randomUUID()
    : Math.random().toString(36).slice(2, 10));

  //: Publish this viewer into the running demo.
  //:
  //: :param options: ``camera`` (whose world pose is the head), ``worldRoot``,
  //:     ``live`` (returning the live bridge's url, or null when no demo is
  //:     running) and ``hands`` (returning any further parts to report, as
  //:     ``[{name, object}]``).
  //: :return: ``publish()`` to be called once per frame while the viewer is in the
  //:     scene, and ``withdraw()`` when they leave.
  function install(options) {
    const camera = options.camera;
    const worldRoot = options.worldRoot;
    const live = options.live;
    const hands = options.hands || function () { return []; };
    const onIdentity = options.onIdentity || function () {};

    const worldFromScene = new THREE.Matrix4();
    const sceneToWorld = new THREE.Quaternion();
    const partPosition = new THREE.Vector3();
    const partQuaternion = new THREE.Quaternion();
    const facing = new THREE.Quaternion();

    let postedAt = 0;
    let postedParts = null;      // the poses last posted, for the thresholds
    // as the bridge last admitted this viewer; ``head`` is the marker id of their own
    // head, which their own view leaves out (see ``ownHead``)
    let identity = { name: '', color: '', head: null };

    //: Send one body to the bridge's avatar route, ignoring the outcome.
    //:
    //: A demo can end at any moment and the bridge goes with it; a viewer losing
    //: their avatar is not a reason to interrupt them.
    //:
    //: The bridge answers with who it admitted this viewer as — the name over their
    //: head and the colour they are drawn in, both chosen there because only the
    //: bridge sees everyone in the room — and the marker id their head is drawn under.
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
        }).then(function (response) {
          return response.ok ? response.json() : null;
        }).then(function (answer) {
          if (!answer || !answer.name || answer.name === identity.name) return;
          identity = { name: answer.name, color: answer.color, head: answer.head };
          onIdentity(identity);
        }).catch(function () {});
      } catch (e) { /* the bridge is optional */ }
    }

    function publish() {
      const base = live && live();
      if (!base) { postedParts = null; return; }
      const now = (global.performance && global.performance.now()) || Date.now();
      const since = now - postedAt;
      if (since < MOVE_INTERVAL_MS) return;

      // three's y-up world back into the z-up frame every published pose uses
      if (worldRoot) {
        worldRoot.updateWorldMatrix(true, false);
        worldFromScene.copy(worldRoot.matrixWorld).invert();
        worldRoot.getWorldQuaternion(sceneToWorld).invert();
      } else {
        worldFromScene.identity();
        sceneToWorld.identity();
      }

      const parts = [{ name: 'head', object: camera }].concat(hands());
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
          || was.position.distanceTo(pose.position) >= MOVE_THRESHOLD
          || was.quaternion.angleTo(pose.quaternion) >= TURN_THRESHOLD) changed = true;
      }
      // holding still is not leaving: report in anyway, or the bridge sweeps this
      // viewer as gone the moment somebody else's post runs the sweep
      if (!changed && since < HEARTBEAT_MS) return;

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

    //: Take this viewer's avatar back out of the scene.
    //:
    //: :param leaving: Whether the page itself is going away.
    function withdraw(leaving) {
      const base = live && live();
      postedParts = null;
      if (base) post(base, { viewer: VIEWER_ID, gone: true }, leaving);
    }

    return {
      publish: publish,
      withdraw: withdraw,
      id: VIEWER_ID,
      identity: function () { return identity; },
      //: Whether a published marker is this viewer's own head.
      //:
      //: The head marker comes back a network round trip late, so drawn in this
      //: viewer's own view it trails the real head: step back and it is suddenly in
      //: front of the eyes, flickering in and out as the posts catch up.
      ownHead: function (marker) {
        return identity.head !== null && marker.ns === AVATAR_NAMESPACE && marker.id === identity.head;
      },
    };
  }

  global.ViewerPresence = {
    VIEWER_ID: VIEWER_ID,
    install: install,
  };
})(window);

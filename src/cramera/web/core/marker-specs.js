/* ============================================================================
 * core/marker-specs.js — how a published CRAM debug marker becomes renderable.
 *
 * The bridge serves /markers entries with the visualization_msgs vocabulary (kind,
 * pose, scale with per-type meaning, points). This module holds the pure mapping
 * from one entry to the build instruction the 3D code executes, so the geometry
 * decisions (which primitive, what the scale means) are testable without THREE.
 * ==========================================================================*/
(function (global) {
  'use strict';

  /* One marker entry as a build instruction:
     {type, pose, color, opacity, and per type:
      'box'/'sphere' → size [x,y,z] (extents / diameters);
      'cylinder' → size (x,y diameters, z height, axis along Z);
      'arrow' → length (scale.x), shaftDiameter (scale.y), headDiameter (scale.z);
      'line' (strip) / 'segments' (pairs) → points, width (scale.x);
      'points' → points, size (scale.x), shape ('square'|'sphere'|'cube');
      'text' → text, height (scale.z)}.
     Returns null for an entry the viewer cannot build. */
  function buildSpec(marker) {
    if (!marker || !marker.kind) return null;
    const common = {
      pose: marker.pose || [0, 0, 0, 0, 0, 0, 1],
      color: marker.color || '#ffb648',
      opacity: typeof marker.opacity === 'number' ? marker.opacity : 1,
    };
    const scale = marker.scale || [1, 1, 1];
    switch (marker.kind) {
      case 'cube':
        return Object.assign(common, { type: 'box', size: scale });
      case 'sphere':
        return Object.assign(common, { type: 'sphere', size: scale });
      case 'cylinder':
        return Object.assign(common, { type: 'cylinder', size: scale });
      case 'arrow':
        return Object.assign(common, {
          type: 'arrow',
          length: scale[0] || 0.3,
          shaftDiameter: scale[1] || 0.03,
          headDiameter: (scale[2] || scale[1] || 0.03) * 2,
        });
      case 'line_strip':
        return Object.assign(common, { type: 'line', points: marker.points || [], width: scale[0] });
      case 'line_list':
        return Object.assign(common, { type: 'segments', points: marker.points || [], width: scale[0] });
      case 'points':
      case 'sphere_list':
      case 'cube_list':
        return Object.assign(common, {
          type: 'points',
          points: marker.points || [],
          size: scale[0] || 0.02,
          shape: marker.kind === 'points' ? 'square' : marker.kind.replace('_list', ''),
        });
      case 'text':
        return Object.assign(common, { type: 'text', text: marker.text || '', height: scale[2] || 0.1 });
      default:
        return null;
    }
  }

  /* Separates the parts of a marker key. */
  const KEY_SEPARATOR = String.fromCharCode(0);

  /* One marker's identity: topic, namespace and id, the way RViz keys them.
     The overlay reconciles against the published set rather than rebuilding it, and
     this is what says which drawn object a published marker is the next state of.

     The NUL separator cannot appear in a ROS topic or namespace, which are limited
     to alphanumerics and / _ ~ — so the three parts cannot run together into
     another marker's key. */
  function key(marker) {
    return marker.topic + KEY_SEPARATOR + marker.ns + KEY_SEPARATOR + marker.id;
  }

  /* Whether two published markers differ in nothing but where they are — i.e.
     whether the object built for `a` can be reused for `b` by moving it.

     Everything buildSpec reads apart from the pose has to match, since the pose is
     all a holder can be moved to. Point lists are walked element-wise rather than
     serialized: a long one then costs a scan and no allocation, still far less than
     rebuilding its buffer. */
  function sameShape(a, b) {
    if (a.kind !== b.kind || a.color !== b.color
      || a.opacity !== b.opacity || a.text !== b.text) return false;
    const aScale = a.scale || [], bScale = b.scale || [];
    if (aScale.length !== bScale.length) return false;
    for (let i = 0; i < aScale.length; i++) if (aScale[i] !== bScale[i]) return false;
    const aPoints = a.points || [], bPoints = b.points || [];
    if (aPoints.length !== bPoints.length) return false;
    for (let i = 0; i < aPoints.length; i++) {
      if (aPoints[i][0] !== bPoints[i][0] || aPoints[i][1] !== bPoints[i][1]
        || aPoints[i][2] !== bPoints[i][2]) return false;
    }
    return true;
  }

  global.MarkerSpecs = {
    buildSpec: buildSpec,
    key: key,
    sameShape: sameShape,

    /* Every renderable build instruction of a /markers payload. */
    buildSpecs: function (markers) {
      return (markers || []).map(buildSpec).filter(function (spec) { return spec !== null; });
    },
  };
})(typeof window !== 'undefined' ? window : this);

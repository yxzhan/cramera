/* ============================================================================
 * core/exposure.js — how bright the scene is rendered, and how to try another one.
 *
 * The scene's lights are tuned around one exposure, which multiplies the image
 * before tone mapping: it changes the level without touching the ratios between
 * the key, fill and ambient lights, which is what editing a light would do.
 *
 * A scene may carry its own (``rendering.exposure`` in its scene.json), and
 * ``?exposure=0.7`` overrides both for one page load. Judging an exposure means
 * looking at the real scene, and inside a headset there is no other way to try a
 * value — nobody is editing source with a headset on.
 *
 * Pure parsing, no THREE and no DOM, so the clamping is testable under node.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: the exposure the scene's lighting is tuned for
  const DEFAULT = 0.3;
  //: the range an override is held within — outside it the image is black or blown
  //: out, which reads as a broken page rather than as a bad value
  const MIN = 0.05;
  const MAX = 1;
  const PARAMETER = /[?&]exposure=([^&]*)/;

  //: The exposure to render at: what the page URL asks for, else what the scene
  //: declares, else :data:`DEFAULT`.
  //:
  //: :param search: The URL's query string, ``window.location.search``.
  //: :param declared: The scene's own ``rendering.exposure``, if it has one.
  function of(search, declared) {
    const fallback = Number.isFinite(declared) && declared > 0 ? declared : DEFAULT;
    const asked = PARAMETER.exec(search || '');
    if (!asked) return fallback;
    const value = parseFloat(decodeURIComponent(asked[1]));
    if (!isFinite(value) || value <= 0) return fallback;
    return Math.min(Math.max(value, MIN), MAX);
  }

  global.Exposure = {
    DEFAULT: DEFAULT,
    MIN: MIN,
    MAX: MAX,
    of: of,
  };
})(typeof window !== 'undefined' ? window : this);

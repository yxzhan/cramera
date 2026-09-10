/* ============================================================================
 * core/scene.js — the active scene and the page layout, as read from the page URL.
 *
 * robot_scene/panel.js switches scenes by reloading the page with a new
 * ?scene= query param; every panel that talks to the /api/* routes must read
 * the same param so its requests target that same scene instead of whatever
 * the server falls back to.
 *
 * The same URL says whether the page shows the 3D scene alone: ?layout=scene
 * opens it that way in a window of its own (the pop-out for a second screen),
 * and a page another page embeds in an iframe with ?scene or ?replay= is the
 * Plan Builder's or the replay popup's scene view.
 * ==========================================================================*/
(function () {
  'use strict';

  //: value of the ?layout= param that shows the 3D scene alone
  const LAYOUT_SCENE = 'scene';
  //: class on the root element while the 3D scene is shown alone; app.css styles it
  const SCENE_ONLY_CLASS = 'scene-only';
  //: class on the root element of the window the scene was popped out into
  const POPPED_OUT_CLASS = 'popped-out';
  const LAYOUT_SCENE_PATTERN = new RegExp('[?&]layout=' + LAYOUT_SCENE + '(&|$)');
  //: the viewer page a pop-out window opens
  const VIEWER_PAGE = 'index.html';
  //: the port the live bridge serves on (cramera.live.http.DEFAULT_PORT)
  const LIVE_BRIDGE_PORT = '8765';

  function name() {
    const m = /[?&]scene=([\w-]+)/.exec(window.location.search);
    return m ? m[1] : null;
  }

  //: Resolve a server route against the page's own base.
  //:
  //: The viewer is not always served from the root of its origin: behind a JupyterHub
  //: or code-server proxy it lives under a /user/<name>/proxy/<port>/ prefix. A
  //: root-absolute '/api/...' would drop that prefix and hit the host itself (the hub
  //: answers those with its own 404), so every route is resolved relative to the page.
  function url(route) {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(route)) return route;   // already absolute
    const base = typeof document === 'undefined' ? null : document.baseURI;
    if (!base) return route;
    return new URL(String(route).replace(/^\/+/, ''), base).href;
  }

  function withScene(route) {
    const resolved = url(route);
    const active = name();
    if (!active) return resolved;
    return resolved + (resolved.indexOf('?') >= 0 ? '&' : '?') + 'scene=' + encodeURIComponent(active);
  }

  //: Where the live bridge is reachable from this page.
  //:
  //: ``?live=`` overrides it, as a full URL, as a path on this origin, or as
  //: host:port. Without it, a page served under a .../proxy/<port>/ prefix reads the
  //: bridge through that same proxy, so the request keeps the page's origin and
  //: scheme: a browser on an HTTPS page blocks a plain http:// call to the bridge's
  //: port as mixed content, and behind a proxy that port is not routable anyway.
  function liveUrl() {
    const override = /[?&]live=([^&]+)/.exec(window.location.search);
    if (override) {
      const value = decodeURIComponent(override[1]).replace(/\/+$/, '');
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value) || value.charAt(0) === '/') return value;
      return window.location.protocol + '//' + value;
    }
    const proxied = /^(.*\/proxy\/)\d+(?:\/|$)/.exec(window.location.pathname);
    if (proxied) return proxied[1] + LIVE_BRIDGE_PORT;
    return window.location.protocol + '//' + window.location.hostname + ':' + LIVE_BRIDGE_PORT;
  }

  //: whether this window is the one the scene was popped out into
  function poppedOut() {
    return LAYOUT_SCENE_PATTERN.test(window.location.search);
  }

  //: whether the page shows the 3D scene alone; ``framed`` says the page sits in an iframe
  function sceneOnly(framed) {
    if (poppedOut()) return true;
    return framed && /[?&](replay=|scene(\b|=))/.test(window.location.search);
  }

  //: the url that opens the active scene alone in a window of its own
  function popOutUrl() {
    return withScene(VIEWER_PAGE) + (name() ? '&' : '?') + 'layout=' + LAYOUT_SCENE;
  }

  window.SceneContext = {
    LAYOUT_SCENE: LAYOUT_SCENE,
    SCENE_ONLY_CLASS: SCENE_ONLY_CLASS,
    POPPED_OUT_CLASS: POPPED_OUT_CLASS,
    name: name,
    url: url,
    liveUrl: liveUrl,
    withScene: withScene,
    sceneOnly: sceneOnly,
    poppedOut: poppedOut,
    popOutUrl: popOutUrl,
  };
})();

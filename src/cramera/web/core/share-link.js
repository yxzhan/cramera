/* ============================================================================
 * core/share-link.js — inviting other people into this session.
 *
 * A session is reached through a link that carries its access token, so whoever
 * has the link can join. A phone scans it as a QR code. A headset can scan
 * nothing and typing a link that long on a VR keyboard is hopeless, so a headset
 * gets a short pairing code instead: the pairing service at PAIRING_SERVICE holds
 * code -> link for a while, and a headset that opens that service and types the
 * code is sent on to the link.
 *
 * The link rules are pure, no DOM, so they are testable under node.
 * ==========================================================================*/
(function (global) {
  'use strict';

  //: the query parameter the session's access token travels in
  const TOKEN_PARAM = 'token';
  //: sessionStorage key the token is remembered under once the page has seen it
  const TOKEN_KEY = 'cramera.session-token';
  //: where headsets redeem pairing codes; overridden with ``?pair=<url>``
  const PAIRING_SERVICE = 'https://vr.aicor.dev';
  const PAIRING_OVERRIDE = /[?&]pair=([^&]+)/;

  //: The token this page was opened with, remembered for the rest of the tab.
  //:
  //: The token only arrives in the first url; once the server has set its cookie,
  //: navigating inside the page (a scene switch) can drop it from the address bar
  //: while the page keeps working, which would leave nothing to share.
  //:
  //: :param search: The url's query string, ``window.location.search``.
  //: :param storage: Where to remember it — ``window.sessionStorage``, or null.
  //: :return: The token, or null when the page was never given one.
  function sessionToken(search, storage) {
    const token = new URLSearchParams(search).get(TOKEN_PARAM);
    if (token) {
      try { if (storage) storage.setItem(TOKEN_KEY, token); } catch (e) { /* private mode */ }
      return token;
    }
    try { return (storage && storage.getItem(TOKEN_KEY)) || null; } catch (e) { return null; }
  }

  //: The session token, asking the viewer's server for it when the page was opened
  //: without one — from the Jupyter launcher, say, where the proxy's cookie let it
  //: in with none in the url.
  //:
  //: :param known: The token the page already has, or null.
  //: :param route: The server's token route, resolved against the page.
  //: :return: A promise of the token, or of null when there is none to be had.
  function resolveToken(known, route) {
    if (known) return Promise.resolve(known);
    return global.fetch(route).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (answer) {
      return (answer && answer.token) || null;
    }).catch(function () { return null; });
  }

  //: The link that brings someone else into the session: ``page`` with the session
  //: token added.
  //:
  //: :param page: The page to send them to, as an absolute url.
  //: :param token: The session token, or null.
  function link(page, token) {
    const url = new URL(page);
    url.hash = '';
    if (token) url.searchParams.set(TOKEN_PARAM, token);
    return url.toString();
  }

  //: Where the pairing service is, as a base url without a trailing slash.
  //:
  //: :param search: The url's query string.
  function pairingService(search) {
    const override = PAIRING_OVERRIDE.exec(search || '');
    const base = override ? decodeURIComponent(override[1]) : PAIRING_SERVICE;
    return base.replace(/\/+$/, '');
  }

  //: Ask the pairing service for a code a headset can redeem for ``target``.
  //:
  //: :param service: The pairing service's base url.
  //: :param target: The link the code should lead to.
  //: :return: A promise of ``{code, expiresAt}`` — ``expiresAt`` in ms since the
  //:     epoch — rejecting when the service cannot be reached or refuses.
  function requestCode(service, target) {
    return global.fetch(service + '/api/pairings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: target }),
      credentials: 'omit',
    }).then(function (response) {
      if (!response.ok) throw new Error('pairing service answered ' + response.status);
      return response.json();
    }).then(function (answer) {
      if (!answer || !answer.code) throw new Error('pairing service sent no code');
      return { code: String(answer.code), expiresAt: Date.parse(answer.expires_at) || null };
    });
  }

  //: The url shown to headset users, without its scheme — the part they type.
  function typedAddress(service) {
    return service.replace(/^https?:\/\//, '');
  }

  global.ShareLink = {
    TOKEN_PARAM: TOKEN_PARAM,
    PAIRING_SERVICE: PAIRING_SERVICE,
    sessionToken: sessionToken,
    resolveToken: resolveToken,
    link: link,
    pairingService: pairingService,
    requestCode: requestCode,
    typedAddress: typedAddress,
  };
})(typeof window !== 'undefined' ? window : this);

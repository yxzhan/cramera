/**
 * The Shared Sim page: Robot Lobby in a frame under cramera's top bar.
 *
 * The lobby is published on its own, at LOBBY; `?lobby=<url>` on this page shows
 * another copy instead (a local `npm start`, a test build).
 * This page's other parameters go to the lobby as they are: `?server=<url>` joins that
 * shared world, `?local` keeps the physics in the page, `?stage=<id>` opens a stage.
 * Without `server` or `local` the lobby gets `?online`: it starts its shared world on
 * Binder as it opens (Start shared world, without the click), unless the last one it
 * joined in this browser still answers, which it rejoins.
 * The lobby gets this page's address as `?embed=`, so the links it gives others (its
 * Share panel, a stage's link) open this page rather than the lobby alone.
 *
 * The two keep their parameters the same through messages (the lobby's
 * public/js/app/embed.js):
 * - `{type: 'robot-lobby:url', params}` from the lobby: its parameters as it opens and
 *   whenever they change (it joined a server, it took a stage); this page's address
 *   follows them, without a new history entry, so a reload or a copied link opens the
 *   same world.
 * - `{type: 'robot-lobby:params', params}` to the lobby: this page's parameters after
 *   the back or forward button changed them; the lobby opens with them.
 * `params` is a list of [name, value] pairs without this page's own `lobby` and the
 * lobby's own `embed` and `online`. Messages are taken only from the lobby's frame and
 * origin and sent only to that origin, as a `server` carries its access token.
 */

const LOBBY = 'https://yxzhan.github.io/robot-lobby/';

/** This page's parameters that are not the lobby's. */
const OWN = ['lobby'];

/** The lobby's parameters that are not this page's. */
const LOBBYS_OWN = ['embed', 'online'];

const frame = document.querySelector('.shared-sim-frame');
const params = new URLSearchParams(location.search);

const source = new URL(params.get('lobby') || LOBBY, location.href);

/**
 * @param {URLSearchParams} search This page's parameters.
 * @returns {string[][]} Those the lobby shares with this page.
 */
const shared = (search) =>
  [...search].filter(([name]) => !OWN.includes(name) && !LOBBYS_OWN.includes(name));

/**
 * @param {string[][]} pairs Parameters both pages share.
 * @returns {boolean} Whether this page's address already holds exactly these.
 */
const holds = (pairs) =>
  JSON.stringify(shared(new URLSearchParams(location.search))) === JSON.stringify(pairs);

for (const [name, value] of shared(params)) source.searchParams.set(name, value);
if (!params.has('local') && !params.get('server')) source.searchParams.set('online', '');

// This page with only `?lobby=`: the lobby adds its own parameters to it.
const embed = new URL(location.pathname, location.href);
if (params.get('lobby')) embed.searchParams.set('lobby', params.get('lobby'));
source.searchParams.set('embed', embed.href);

/**
 * The parameters last asked of the lobby, until it has them: a message sent while the
 * lobby is still opening is lost, and its first answer then still holds the old ones.
 * @type {string[][] | null}
 */
let asked = null;

/** How often the lobby is asked again before its own parameters are taken. */
const ASKS = 3;

let asks = 0;

/**
 * Asks the lobby to open with this page's parameters (the lobby answers with its own).
 * @param {boolean} again Whether it is the same request again, after an answer without them.
 */
const ask = (again = false) => {
  asks = again ? asks + 1 : 1;
  asked = shared(new URLSearchParams(location.search));
  frame.contentWindow?.postMessage({ type: 'robot-lobby:params', params: asked }, source.origin);
};

window.addEventListener('message', (event) => {
  if (event.source !== frame.contentWindow || event.origin !== source.origin) return;
  const { type, params: pairs } = event.data ?? {};
  if (type !== 'robot-lobby:url' || !Array.isArray(pairs)) return;
  if (asked !== null) {
    if (JSON.stringify(pairs) !== JSON.stringify(asked) && asks < ASKS) return ask(true);
    asked = null;
  }
  if (holds(pairs)) return;
  const next = new URL(embed.href);
  for (const [name, value] of pairs) {
    if (!OWN.includes(String(name))) next.searchParams.append(String(name), String(value));
  }
  next.hash = location.hash;
  history.replaceState(history.state, '', next);
});

// Back and forward: the lobby opens with the parameters this address now has.
window.addEventListener('popstate', () => ask());

frame.src = source.href;

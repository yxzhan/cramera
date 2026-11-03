/**
 * The Shared Sim page: Robot Lobby in a frame under cramera's top bar.
 *
 * The lobby is published on its own, at LOBBY; `?lobby=<url>` on this page shows
 * another copy instead (a local `npm start`, a test build).
 * The lobby's own parameters go to it as they are: `?server=<url>` joins that shared
 * world, `?local` keeps the physics in the page. Without either the lobby gets
 * `?online`: it starts its shared world on Binder as it opens (Start shared world,
 * without the click), unless the last one it joined in this browser still answers,
 * which it rejoins.
 */

const LOBBY = 'https://yxzhan.github.io/robot-lobby/';

const frame = document.querySelector('.shared-sim-frame');
const params = new URLSearchParams(location.search);

const source = new URL(params.get('lobby') || LOBBY, location.href);
if (params.has('local')) source.searchParams.set('local', '');
else if (params.get('server')) source.searchParams.set('server', params.get('server'));
else source.searchParams.set('online', '');
frame.src = source.href;

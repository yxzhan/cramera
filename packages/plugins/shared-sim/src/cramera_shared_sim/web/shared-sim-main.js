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
 */

const LOBBY = 'https://yxzhan.github.io/robot-lobby/';

const frame = document.querySelector('.shared-sim-frame');
const params = new URLSearchParams(location.search);

const source = new URL(params.get('lobby') || LOBBY, location.href);
for (const [name, value] of params) if (name !== 'lobby') source.searchParams.set(name, value);
if (!params.has('local') && !params.get('server')) source.searchParams.set('online', '');

// This page with only `?lobby=`: the lobby adds its own parameters to it.
const embed = new URL(location.pathname, location.href);
if (params.get('lobby')) embed.searchParams.set('lobby', params.get('lobby'));
source.searchParams.set('embed', embed.href);

frame.src = source.href;

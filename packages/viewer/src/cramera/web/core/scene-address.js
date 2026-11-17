/**
 * How a page address names what the page shows: `?scene=<name>`, `?live=<address>`,
 * `?layout=scene`, `?panel=<identity>` and `?fullscreen`.
 */

import { PageLayout, PageParameter } from '../protocol.js';

/**
 * The query parameter naming the shown bundle.
 */
export const SCENE_PARAMETER = PageParameter.SCENE;

/**
 * The query parameter naming the live publisher the page follows.
 */
export const LIVE_PARAMETER = PageParameter.LIVE;

// %% addresses

/**
 * @param {string} href A page address.
 * @returns {string|null} The bundle it names, or null.
 */
export function sceneOf(href) {
  return new URL(href).searchParams.get(SCENE_PARAMETER);
}

/**
 * @param {string} href A page address.
 * @param {string} name A bundle name.
 * @returns {URL} The address naming that bundle.
 */
export function withScene(href, name) {
  const url = new URL(href);
  url.searchParams.set(SCENE_PARAMETER, name);
  return url;
}

/**
 * @param {string} href A page address.
 * @returns {string|null} The live publisher it names, or null.
 */
export function liveAddressOf(href) {
  return new URL(href).searchParams.get(LIVE_PARAMETER);
}

/**
 * @param {string} href A page address.
 * @param {string} address A live publisher's address.
 * @returns {URL} The address following that publisher instead of a bundle.
 */
export function withLive(href, address) {
  const url = new URL(href);
  url.searchParams.delete(SCENE_PARAMETER);
  url.searchParams.set(LIVE_PARAMETER, address);
  return url;
}

/**
 * @param {string} href A page address.
 * @returns {string|null} The `PageLayout` value it names, or null for the full page.
 */
export function layoutOf(href) {
  return new URL(href).searchParams.get(PageParameter.LAYOUT);
}

/**
 * @param {string} href The address of the page as it is shown.
 * @returns {URL} The address of the scene alone, as a popped-out window shows it.
 */
export function sceneAlone(href) {
  const url = new URL(href);
  url.searchParams.delete(PageParameter.PANEL);
  url.searchParams.set(PageParameter.LAYOUT, PageLayout.SCENE);
  return url;
}

/**
 * @param {string} href A page address.
 * @returns {string|null} The identity of the panel it shows first, or null.
 */
export function panelOf(href) {
  return new URL(href).searchParams.get(PageParameter.PANEL);
}

/**
 * @param {string} viewer The address of the viewer's page.
 * @param {string} panel A panel's identity.
 * @returns {URL} The viewer's page showing that panel first.
 */
export function withPanel(viewer, panel) {
  const url = new URL(viewer);
  url.searchParams.set(PageParameter.PANEL, panel);
  return url;
}

/**
 * @param {string} href A page address.
 * @returns {boolean} Whether it asks for the page on the full screen.
 */
export function fullscreenOf(href) {
  return new URL(href).searchParams.has(PageParameter.FULLSCREEN);
}

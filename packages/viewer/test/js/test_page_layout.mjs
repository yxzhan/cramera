import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ONTOLOGY_PAGE, ONTOLOGY_ROUTE, ONTOLOGY_TITLE, SCENE_PAGE, SiteHeader, ThemeSetting, localStorageOf, pageLinksOf, versionOf } from '../../src/cramera/web/app/site-header.js';
import { fullscreenOf, layoutOf, panelOf, sceneAlone, withPanel } from '../../src/cramera/web/core/scene-address.js';
import { FirstGestureFullscreen, FULLSCREEN_GESTURES } from '../../src/cramera/web/app/fullscreen.js';
import { QuerySlot, SlotPanel, SlotTaken } from '../../src/cramera/web/panels/query-slot.js';
import { COLUMNS, PaneSplit, ROWS } from '../../src/cramera/web/app/pane-split.js';
import {
  BuildKey,
  NavigationGroup,
  NavigationKey,
  PageLayout,
  PageParameter,
  PageStorageKey,
  PluginKey,
  Theme,
  ViewerInfoKey,
} from '../../src/cramera/web/protocol.js';
import { FakeDocument, FakeStorage } from './support/fake-document.mjs';

const BASE = 'https://sunava.github.io/cramera/';

const url = (route) => new URL(route.replace(/^\//, ''), BASE).href;

/**
 * The viewer's information with plugins declaring pages and panels in mixed order.
 */
const INFO = {
  [ViewerInfoKey.VERSION]: '1.0.0',
  [ViewerInfoKey.BUILD]: { [BuildKey.COMMIT]: 'abcdef0123456789', [BuildKey.BUILT_AT]: '2026-10-23T10:00:00Z' },
  [ViewerInfoKey.PLUGINS]: [
    { [PluginKey.NAME]: 'questions' },
    { [PluginKey.NAME]: 'scene-builder', [PluginKey.NAVIGATION]: { [NavigationKey.TITLE]: 'Scene Builder', [NavigationKey.GROUP]: NavigationGroup.BUILDER, [NavigationKey.PAGE]: '/plugins/scene-builder/builder.html' } },
    { [PluginKey.NAME]: 'models', [PluginKey.NAVIGATION]: { [NavigationKey.TITLE]: 'Models', [NavigationKey.GROUP]: NavigationGroup.ANALYSIS } },
    { [PluginKey.NAME]: 'plan-builder', [PluginKey.NAVIGATION]: { [NavigationKey.TITLE]: 'Plan Builder', [NavigationKey.GROUP]: NavigationGroup.BUILDER, [NavigationKey.PAGE]: '/plugins/plan-builder/builder.html' } },
  ],
};

// %% navigation

test('the pages are the scene page, then the plugins\' entries by group and title, then the ontology', () => {
  const links = pageLinksOf(INFO, url);
  assert.deepEqual(links.map((link) => link.title), ['Scene', 'Models', 'Plan Builder', 'Scene Builder', ONTOLOGY_TITLE]);
  assert.equal(links[0].href, BASE);
  assert.equal(links[2].href, `${BASE}plugins/plan-builder/builder.html`);
  assert.equal(links[1].panel, 'models');
  assert.equal(links[1].href, `${BASE}?${PageParameter.PANEL}=models`);
  assert.equal(links[2].panel, null);
});

test('without plugin entries only the viewer\'s own pages are offered', () => {
  assert.deepEqual(pageLinksOf({ [ViewerInfoKey.PLUGINS]: [{ [PluginKey.NAME]: 'questions' }] }, url).map((link) => link.identity), [SCENE_PAGE, ONTOLOGY_PAGE]);
});

test('the ontology is a page of the viewer\'s own', () => {
  const ontology = pageLinksOf(INFO, url).at(-1);
  assert.equal(ontology.identity, ONTOLOGY_PAGE);
  assert.equal(ontology.href, url(ONTOLOGY_ROUTE));
  assert.equal(ontology.panel, null);
});

test('a viewer given another logo names what it shows', () => {
  const document = new FakeDocument();
  const root = document.createElement('header');
  const header = new SiteHeader(root, url, new ThemeSetting(document.documentElement, null), SCENE_PAGE);
  header.show({ ...INFO, [ViewerInfoKey.LOGO_TEXT]: 'Our lab' });
  assert.equal(root.children[0].children[0].alt, 'Our lab');
});

test('the header marks its own page and shows panels on the scene page', () => {
  const document = new FakeDocument();
  const root = document.createElement('header');
  const shown = [];
  const header = new SiteHeader(root, url, new ThemeSetting(document.documentElement, null), SCENE_PAGE, (panel) => shown.push(panel));
  header.show(INFO);
  const [brand, navigation, actions] = root.children;
  assert.equal(brand.children[0].src, `${BASE}api/logo`);
  assert.match(brand.children[0].alt, /^AICOR/);
  const anchors = navigation.children;
  assert.equal(anchors[0].getAttribute('aria-current'), 'page');
  assert.equal(anchors[1].getAttribute('aria-current'), null);
  let prevented = false;
  anchors[1].dispatch('click', { preventDefault: () => { prevented = true; } });
  assert.deepEqual(shown, ['models']);
  assert.equal(prevented, true);
  assert.equal(anchors[2].listeners.has('click'), false);
  assert.equal(actions.children[0].textContent, versionOf(INFO));
  assert.equal(actions.children[0].href, `${BASE}status.html`);
});

test('a plugin panel shown on the scene page is marked as the current page', () => {
  const document = new FakeDocument();
  const root = document.createElement('header');
  const header = new SiteHeader(root, url, new ThemeSetting(document.documentElement, null), SCENE_PAGE, () => {});
  header.show(INFO);
  header.markCurrent('models');
  const current = root.children[1].children.filter((anchor) => anchor.getAttribute('aria-current') === 'page');
  assert.deepEqual(current.map((anchor) => anchor.dataset.page), ['models']);
  header.markCurrent(SCENE_PAGE);
  assert.equal(root.children[1].children[0].getAttribute('aria-current'), 'page');
  assert.equal(root.children[1].children[1].getAttribute('aria-current'), 'false');
});

test('on a plugin page the header follows the panel links instead', () => {
  const document = new FakeDocument();
  const root = document.createElement('header');
  new SiteHeader(root, url, new ThemeSetting(document.documentElement, null), 'plan-builder').show(INFO);
  const anchors = root.children[1].children;
  assert.equal(anchors[1].listeners.has('click'), false);
  assert.equal(anchors[2].getAttribute('aria-current'), 'page');
});

test('the build stamp names the version, the short commit and the build time', () => {
  assert.equal(versionOf(INFO), 'v1.0.0 · abcdef0 · 2026-10-23T10:00:00Z');
  assert.equal(versionOf({ [ViewerInfoKey.VERSION]: '1.0.0' }), 'v1.0.0');
});

// %% theme

test('the theme is dark unless light was chosen, and the choice is kept', () => {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const theme = new ThemeSetting(document.documentElement, storage);
  theme.apply();
  assert.equal(document.documentElement.dataset.theme, Theme.DARK);
  theme.choose(Theme.LIGHT);
  assert.equal(document.documentElement.dataset.theme, Theme.LIGHT);
  assert.equal(storage.getItem(PageStorageKey.THEME), Theme.LIGHT);
  assert.equal(new ThemeSetting(document.documentElement, storage).theme, Theme.LIGHT);
  storage.setItem(PageStorageKey.THEME, 'sepia');
  assert.equal(new ThemeSetting(document.documentElement, storage).theme, Theme.DARK);
});

test('a page whose storage is refused still shows and changes its theme', () => {
  const document = new FakeDocument();
  const refusing = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  const theme = new ThemeSetting(document.documentElement, refusing);
  assert.equal(theme.theme, Theme.DARK);
  theme.choose(Theme.LIGHT);
  assert.equal(document.documentElement.dataset.theme, Theme.LIGHT);
  const window = { get localStorage() { throw new Error('denied'); } };
  assert.equal(localStorageOf(window), null);
});

test('the top bar switches the theme with one click and shows which is chosen', () => {
  const document = new FakeDocument();
  const root = document.createElement('header');
  new SiteHeader(root, url, new ThemeSetting(document.documentElement, new FakeStorage()), SCENE_PAGE);
  const buttons = root.children[2].children[1].children;
  const pressed = () => buttons.filter((button) => button.getAttribute('aria-pressed') === 'true').map((button) => button.dataset.theme);
  assert.deepEqual(buttons.map((button) => button.dataset.theme), [Theme.DARK, Theme.LIGHT]);
  assert.deepEqual(pressed(), [Theme.DARK]);
  buttons.find((button) => button.dataset.theme === Theme.LIGHT).dispatch('click');
  assert.equal(document.documentElement.dataset.theme, Theme.LIGHT);
  assert.deepEqual(pressed(), [Theme.LIGHT]);
});

// %% addresses

test('the scene alone is the same address with the scene layout and no panel', () => {
  const shown = `${BASE}?scene=cell&${PageParameter.PANEL}=models`;
  const alone = sceneAlone(shown);
  assert.equal(layoutOf(alone.href), PageLayout.SCENE);
  assert.equal(panelOf(alone.href), null);
  assert.equal(alone.searchParams.get(PageParameter.SCENE), 'cell');
  assert.equal(layoutOf(BASE), null);
  assert.equal(panelOf(withPanel(BASE, 'workshop').href), 'workshop');
});

test('a page address asks for the full screen by naming the parameter', () => {
  assert.equal(fullscreenOf(`${BASE}?${PageParameter.FULLSCREEN}`), true);
  assert.equal(fullscreenOf(`${BASE}?scene=cell&${PageParameter.FULLSCREEN}=1`), true);
  assert.equal(fullscreenOf(`${BASE}?scene=cell`), false);
});

// %% full screen

/**
 * A document whose root element counts the full screen requests it gets.
 */
function fullscreenDocument() {
  const document = new FakeDocument();
  document.fullscreenElement = null;
  document.documentElement.requests = 0;
  document.documentElement.requestFullscreen = () => {
    document.documentElement.requests += 1;
    return Promise.resolve();
  };
  return document;
}

test('the page takes the full screen at the first click or key and then stops listening', () => {
  const document = fullscreenDocument();
  new FirstGestureFullscreen(document);
  for (const gesture of FULLSCREEN_GESTURES) assert.equal(document.listenerCount(gesture), 1);
  document.dispatch('pointerdown');
  document.dispatch('keydown');
  assert.equal(document.documentElement.requests, 1);
  for (const gesture of FULLSCREEN_GESTURES) assert.equal(document.listenerCount(gesture), 0);
});

test('a page already shown on the full screen asks for nothing', () => {
  const document = fullscreenDocument();
  document.fullscreenElement = document.documentElement;
  new FirstGestureFullscreen(document);
  document.dispatch('keydown');
  assert.equal(document.documentElement.requests, 0);
});

test('a browser that refuses or lacks the full screen leaves the page as it is', async () => {
  const refusing = fullscreenDocument();
  refusing.documentElement.requestFullscreen = () => Promise.reject(new TypeError('not allowed'));
  new FirstGestureFullscreen(refusing);
  refusing.dispatch('pointerdown');
  const lacking = new FakeDocument();
  new FirstGestureFullscreen(lacking);
  lacking.dispatch('pointerdown');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(lacking.listenerCount('pointerdown'), 0);
});

// %% query slot

test('one plugin fills the query slot, which is hidden until then', () => {
  const document = new FakeDocument();
  const root = document.createElement('section');
  const slot = new QuerySlot(root);
  assert.equal(root.hidden, true);
  assert.equal(slot.occupant, null);
  const aside = document.createElement('span');
  const content = document.createElement('div');
  slot.fill(new SlotPanel('questions', 'EQL', content, aside));
  assert.equal(root.hidden, false);
  assert.equal(slot.occupant, 'questions');
  const [head, body] = root.children;
  assert.deepEqual(head.children.slice(0, 2).map((child) => child.textContent || child), ['EQL', aside]);
  assert.equal(body, content);
  assert.equal(content.classList.contains('slot-body'), true);
  assert.throws(() => slot.fill(new SlotPanel('other', 'Other', document.createElement('div'))), SlotTaken);
});

test('the query slot folds to its heading and remembers it', () => {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const root = document.createElement('section');
  const slot = new QuerySlot(root, storage);
  slot.fill(new SlotPanel('questions', 'EQL', document.createElement('div')));
  const fold = root.children[0].children.at(-1);
  assert.equal(slot.folded, false);
  assert.equal(fold.textContent, '▾');
  fold.dispatch('click');
  assert.equal(slot.folded, true);
  assert.equal(root.classList.contains('folded'), true);
  assert.equal(fold.getAttribute('aria-expanded'), 'false');
  assert.equal(fold.title, 'Hide EQL'.replace('Hide', 'Show'));
  assert.equal(storage.getItem(PageStorageKey.QUERY_FOLDED), 'true');
  const again = new QuerySlot(document.createElement('section'), storage);
  again.fill(new SlotPanel('questions', 'EQL', document.createElement('div')));
  assert.equal(again.folded, true);
  again.setFolded(false);
  assert.equal(storage.getItem(PageStorageKey.QUERY_FOLDED), 'false');
});

// %% pane splits

/**
 * @param {FakeDocument} document The page.
 * @returns {{split: FakeElement, divider: FakeElement}} A split spanning 1000 pixels from the left and top edges.
 */
function splitElements(document) {
  const split = document.createElement('main');
  split.style.setProperty = (name, value) => { split.style[name] = value; };
  split.getBoundingClientRect = () => ({ left: 0, right: 1000, width: 1000, top: 0, bottom: 1000, height: 1000 });
  const divider = document.createElement('div');
  divider.setPointerCapture = () => {};
  return { split, divider };
}

test('the columns start as halves, or as remembered', () => {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const { split, divider } = splitElements(document);
  assert.equal(new PaneSplit(split, divider, COLUMNS, storage).share, 50);
  assert.equal(split.style['--side-share'], '50%');
  storage.setItem(PageStorageKey.SPLIT, '30');
  assert.equal(new PaneSplit(split, divider, COLUMNS, storage).share, 30);
  storage.setItem(PageStorageKey.SPLIT, 'wide');
  assert.equal(new PaneSplit(split, divider, COLUMNS, storage).share, 50);
  assert.equal(new PaneSplit(split, divider, COLUMNS, null).share, 50);
});

test('dragging the divider resizes the side column within bounds and is remembered', () => {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const { split, divider } = splitElements(document);
  const columns = new PaneSplit(split, divider, COLUMNS, storage);
  divider.dispatch('pointerdown', { pointerId: 1 });
  assert.equal(divider.classList.contains('dragging'), true);
  divider.dispatch('pointermove', { clientX: 700 });
  assert.equal(columns.share, 30);
  assert.equal(split.style['--side-share'], '30%');
  divider.dispatch('pointermove', { clientX: 950 });
  assert.equal(columns.share, COLUMNS.least);
  divider.dispatch('pointermove', { clientX: 50 });
  assert.equal(columns.share, COLUMNS.most);
  assert.equal(storage.getItem(PageStorageKey.SPLIT), null);
  divider.dispatch('pointerup');
  assert.equal(divider.classList.contains('dragging'), false);
  assert.equal(storage.getItem(PageStorageKey.SPLIT), '75');
  divider.dispatch('pointermove', { clientX: 500 });
  assert.equal(columns.share, COLUMNS.most);
  divider.dispatch('dblclick');
  assert.equal(columns.share, 50);
  assert.equal(storage.getItem(PageStorageKey.SPLIT), '50');
});

test('dragging the divider below the query slot gives it more or less of the column', () => {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const { split, divider } = splitElements(document);
  const rows = new PaneSplit(split, divider, ROWS, storage);
  assert.equal(rows.share, ROWS.initial);
  assert.equal(split.style['--query-share'], `${ROWS.initial}%`);
  divider.dispatch('pointerdown', { pointerId: 1 });
  divider.dispatch('pointermove', { clientY: 200 });
  assert.equal(rows.share, 20);
  divider.dispatch('pointermove', { clientY: 990 });
  assert.equal(rows.share, ROWS.most);
  divider.dispatch('pointerup');
  assert.equal(storage.getItem(PageStorageKey.QUERY_SPLIT), '85');
  assert.equal(new PaneSplit(split, divider, ROWS, storage).share, 85);
});

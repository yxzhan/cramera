/**
 * Where the plan runs and who performs it: the environment the robots start in, the
 * plan's robots as a list whose entry chosen is the robot whose plan is edited, that
 * robot's model, name and start pose, and adding and removing robots.
 */

import { IndexEntryKey, PlanBuilderCatalogKey, PlanKey, PlannedRobotKey, RobotModelKey, UrdfFolderKey } from '../../../protocol.js';
import { Elements, PlanAction } from './elements.js';
import { PlanPose } from './poses.js';

/**
 * The environment choice of an empty floor.
 */
export const EMPTY_FLOOR = Object.freeze({ value: '', label: 'Empty floor' });

/**
 * The hint in the path field of a URDF to load as an environment.
 */
export const URDF_PLACEHOLDER = '/path/to/world.urdf';

/**
 * Keeps a path's leading slash in front when the picker cuts it on the left.
 */
const LEFT_TO_RIGHT_MARK = '\u200e';

/**
 * What the URDF picker says about a folder without folders or URDF files.
 */
export const EMPTY_FOLDER = 'No folders or URDF files here.';

/**
 * What the environment section says about each kind of choice.
 */
export const EnvironmentNote = Object.freeze({
  EMPTY_FLOOR: 'The robots start on an empty floor.',
  NONE_OFFERED: 'No environment yet: make one in the Scene Builder or load a URDF below. Until then the robots start on an empty floor.',
  /**
   * @param {string} label The environment's name for people.
   * @returns {string} What the section says about a chosen environment.
   */
  chosen: (label) => `Place the robots and objects in ${label} in the 3D view.`,
});

// %% environment

export class EnvironmentView {
  /**
   * The view's element.
   * @type {HTMLElement}
   */
  element;

  /**
   * The environment choices, the empty floor first.
   * @type {Array<{value: string, label: string}>}
   */
  #choices = [EMPTY_FLOOR];

  /**
   * The page that applies the person's edits.
   * @type {Object}
   */
  #editor;

  /**
   * The environment select.
   * @type {HTMLSelectElement}
   */
  select;

  /**
   * The line saying what the chosen environment means.
   * @type {HTMLElement}
   */
  #note;

  /**
   * The path of a URDF or xacro file to load as an environment.
   * @type {HTMLInputElement}
   */
  path;

  /**
   * The picker browsing this machine's folders for a URDF, hidden until opened.
   * @type {HTMLElement}
   */
  picker;

  /**
   * Owns the select's listener.
   * @type {Elements}
   */
  #elements;

  /**
   * Owns the listeners of the folder the picker shows, replaced with every folder.
   * @type {Elements}
   */
  #folder;

  /**
   * @param {Document} document The page's document.
   * @param {Object} editor The page: `state` (the `PlanState`), `edit(change)`,
   *   `loadEnvironment(path)` and `listFolder(path)`, which answers a `UrdfFolder` or
   *   null when the folder cannot be listed.
   */
  constructor(document, editor) {
    this.#editor = editor;
    this.#elements = new Elements(document);
    const elements = this.#elements;
    this.select = elements.select(PlanAction.ENVIRONMENT, this.#choices, '', (value) =>
      this.#editor.edit(() => this.#editor.state.setField(PlanKey.ENVIRONMENT, value || null)),
    );
    this.#note = elements.create('p', 'muted environment-note');
    this.path = elements.input('text', PlanAction.URDF_PATH, () => {});
    this.path.placeholder = URDF_PLACEHOLDER;
    const load = elements.button('Load', PlanAction.LOAD_URDF, () => {
      const path = this.path.value.trim();
      if (path) this.#editor.loadEnvironment(path);
    });
    const browse = elements.button('Browse…', PlanAction.BROWSE_URDF, () => this.browse(null));
    const row = elements.create('div', 'urdf-row');
    row.append(this.path, browse, load);
    this.#folder = new Elements(document);
    this.picker = elements.create('div', 'urdf-picker');
    this.picker.hidden = true;
    this.element = elements.create('div', 'environment');
    this.element.append(this.select, this.#note, elements.label('or load a URDF of this machine', row, 'urdf-load'), this.picker);
    this.render();
  }

  /**
   * Open the picker on a folder.
   * @param {string|null} path The folder, or null for the one the picker showed last.
   * @returns {Promise<void>} Settles once the folder is shown or refused.
   */
  async browse(path) {
    const folder = await this.#editor.listFolder(path);
    if (folder) this.#showFolder(folder);
  }

  /**
   * Close the picker.
   */
  closePicker() {
    this.picker.hidden = true;
    this.#folder.dispose();
    this.picker.replaceChildren();
  }

  /**
   * @param {Object} folder A `UrdfFolder`: its folders to open and its URDF files to
   *   load.
   */
  #showFolder(folder) {
    const document = this.#folder.document;
    this.#folder.dispose();
    const elements = (this.#folder = new Elements(document));
    const here = folder[UrdfFolderKey.PATH];
    const parent = folder[UrdfFolderKey.PARENT] ?? null;
    const up = elements.button('Up', PlanAction.FOLDER_UP, () => this.browse(parent));
    up.disabled = parent === null;
    const head = elements.create('div', 'urdf-picker-head');
    // the path is cut on the left to keep the folder's own name; the mark keeps its
    // leading slash in front
    const shown = elements.create('span', 'urdf-picker-path', `${LEFT_TO_RIGHT_MARK}${here}`);
    shown.title = here;
    head.append(up, shown, elements.button('Close', PlanAction.CLOSE_PICKER, () => this.closePicker()));
    const list = elements.create('div', 'urdf-picker-list');
    for (const name of folder[UrdfFolderKey.FOLDERS]) {
      const open = elements.button(`${name}/`, PlanAction.OPEN_FOLDER, () => this.browse(joined(here, name)));
      open.dataset.name = name;
      list.append(open);
    }
    for (const name of folder[UrdfFolderKey.FILES]) {
      const pick = elements.button(name, PlanAction.PICK_URDF, () => {
        this.path.value = joined(here, name);
        this.closePicker();
        this.#editor.loadEnvironment(this.path.value);
      });
      pick.dataset.name = name;
      list.append(pick);
    }
    if (list.children.length === 0) list.append(elements.create('p', 'muted', EMPTY_FOLDER));
    this.picker.replaceChildren(head, list);
    this.picker.hidden = false;
  }

  /**
   * Offer the catalog's environments, show the chosen one and what it means.
   */
  render() {
    const state = this.#editor.state;
    const chosen = state.field(PlanKey.ENVIRONMENT) || '';
    this.#choices = [
      EMPTY_FLOOR,
      ...state.catalog[PlanBuilderCatalogKey.ENVIRONMENTS].map((entry) => ({ value: entry[IndexEntryKey.NAME], label: entry[IndexEntryKey.ENVIRONMENT] ?? entry[IndexEntryKey.NAME] })),
    ];
    this.#elements.options(this.select, this.#choices, chosen);
    this.#note.textContent = this.note(chosen);
  }

  /**
   * @param {string} chosen The chosen environment's name, or '' for the empty floor.
   * @returns {string} What the choice means.
   */
  note(chosen) {
    if (this.#choices.length === 1) return EnvironmentNote.NONE_OFFERED;
    const choice = this.#choices.find((candidate) => candidate.value === chosen);
    return chosen && choice ? EnvironmentNote.chosen(choice.label) : EnvironmentNote.EMPTY_FLOOR;
  }

  /**
   * Remove every listener.
   */
  dispose() {
    this.#elements.dispose();
    this.#folder.dispose();
  }
}

/**
 * @param {string} folder A folder's absolute path.
 * @param {string} name A name in it.
 * @returns {string} The path of the name in the folder.
 */
function joined(folder, name) {
  return folder.endsWith('/') ? `${folder}${name}` : `${folder}/${name}`;
}

// %% robots

/**
 * What the robots section says while the plan has no robot.
 */
export const RobotsNote = Object.freeze({
  NONE_OFFERED: 'The CRAM workspace offers no robot here.',
  NONE_ADDED: 'No robot yet; add one to compose its plan.',
});

export class RobotsView {
  /**
   * The view's element.
   * @type {HTMLElement}
   */
  element;

  /**
   * The page that applies the person's edits.
   * @type {Object}
   */
  #editor;

  /**
   * Owns the listeners of the shown controls, replaced on every render.
   * @type {Elements}
   */
  #shown;

  /**
   * The model a robot is added as.
   * @type {string|null}
   */
  #addedModel = null;

  /**
   * @param {Document} document The page's document.
   * @param {Object} editor The page: `state` (the `PlanState`), `edit(change)` and
   *   `preferredModel()`, the robot model the person used last or null.
   */
  constructor(document, editor) {
    this.#editor = editor;
    this.#shown = new Elements(document);
    this.element = this.#shown.create('div', 'robots');
  }

  /**
   * Show the robots, which one's plan is edited, and the edited robot's settings.
   */
  render() {
    const document = this.#shown.document;
    this.#shown.dispose();
    const elements = (this.#shown = new Elements(document));
    const state = this.#editor.state;
    const robot = state.activeRobot();
    const models = state.catalog[PlanBuilderCatalogKey.ROBOTS].map((model) => ({ value: model[RobotModelKey.NAME], label: model[RobotModelKey.LABEL] || model[RobotModelKey.NAME] }));
    if (models.length === 0) {
      this.element.replaceChildren(elements.create('p', 'empty', RobotsNote.NONE_OFFERED));
      return;
    }
    const adding = this.#adding(elements, models, robot);
    if (!robot) {
      this.element.replaceChildren(elements.create('p', 'empty', RobotsNote.NONE_ADDED), adding);
      return;
    }
    const identifier = robot[PlannedRobotKey.IDENTIFIER];
    const list = elements.create('ul', 'robot-list');
    list.append(...state.robots().map((entry) => this.#entry(elements, entry, entry === robot)));
    const label = elements.input('text', PlanAction.ROBOT_LABEL, () => this.#editor.edit(() => state.setRobotLabel(identifier, label.value)));
    label.value = robot[PlannedRobotKey.LABEL];
    const model = elements.select(PlanAction.ROBOT_MODEL, models, robot[PlannedRobotKey.MODEL], (value) => this.#editor.edit(() => state.setRobotModel(identifier, value)));
    const pose = robot[PlannedRobotKey.POSE];
    const [x, y, z] = PlanPose.position(pose);
    const place = (position, yaw) => this.#editor.edit(() => state.setRobotPose(identifier, PlanPose.of(...position, yaw)));
    const row = elements.create('div', 'pose-row');
    row.append(
      elements.number('x', x, PlanAction.POSE, (value) => place([value, y, z], PlanPose.yaw(pose))),
      elements.number('y', y, PlanAction.POSE, (value) => place([x, value, z], PlanPose.yaw(pose))),
      elements.number('yaw°', PlanPose.degrees(PlanPose.yaw(pose)), PlanAction.POSE, (value) => place([x, y, z], PlanPose.radians(value))),
    );
    const remove = elements.button(`Remove ${robot[PlannedRobotKey.LABEL]}`, PlanAction.REMOVE_ROBOT, () => this.#editor.edit(() => state.removeRobot(identifier)));
    const settings = elements.create('div', 'robot-settings');
    settings.append(
      elements.create('h3', '', `Settings of ${robot[PlannedRobotKey.LABEL]}`),
      elements.label('name', label),
      elements.label('model', model),
      elements.label('starts at', row),
      remove,
    );
    this.element.replaceChildren(list, settings, adding);
  }

  /**
   * @param {Elements} elements Owns the controls' listeners.
   * @param {Array<{value: string, label: string}>} models The robot models on offer.
   * @param {Object|null} robot The robot whose plan is edited, or null.
   * @returns {HTMLLabelElement} The model a robot is added as and the button adding it;
   *   the model picked before, else the edited robot's, else the one the person used
   *   last or the one offering the most actions.
   */
  #adding(elements, models, robot) {
    const state = this.#editor.state;
    const shown = this.#addedModel ?? robot?.[PlannedRobotKey.MODEL] ?? state.defaultModel(this.#editor.preferredModel());
    const addedModel = elements.select(PlanAction.ROBOT_MODEL, models, shown, (value) => {
      this.#addedModel = value;
    });
    const row = elements.create('div', 'button-row');
    row.append(
      addedModel,
      elements.button('Add', PlanAction.ADD_ROBOT, () => this.#editor.edit(() => state.addRobot(addedModel.value))),
    );
    return elements.label('add a robot', row, 'robot-adding');
  }

  /**
   * @param {Elements} elements Owns the entry's listener.
   * @param {Object} robot A robot of the plan.
   * @param {boolean} edited Whether its plan is the one edited.
   * @returns {HTMLElement} The robot's entry, choosing its plan when clicked.
   */
  #entry(elements, robot, edited) {
    const identifier = robot[PlannedRobotKey.IDENTIFIER];
    const button = elements.button('', PlanAction.SELECT_ROBOT, () => this.#editor.edit(() => this.#editor.state.selectRobot(identifier)));
    button.dataset.robot = identifier;
    button.setAttribute('aria-pressed', String(edited));
    const [x, y] = PlanPose.position(robot[PlannedRobotKey.POSE]);
    const steps = robot[PlannedRobotKey.STEPS].length;
    const head = elements.create('span', 'robot-head');
    head.append(elements.create('strong', '', robot[PlannedRobotKey.LABEL]), elements.create('span', 'badge', this.#editor.state.modelLabel(robot[PlannedRobotKey.MODEL])));
    const facts = `${steps} ${steps === 1 ? 'step' : 'steps'} · starts at ${RobotsView.metres(x)}, ${RobotsView.metres(y)} m`;
    button.append(head, elements.create('span', 'robot-facts', facts));
    if (edited) button.append(elements.create('span', 'robot-editing', 'editing its plan'));
    const item = elements.create('li');
    item.append(button);
    return item;
  }

  /**
   * @param {number} value A coordinate in metres.
   * @returns {string} The coordinate with at most two decimals.
   */
  static metres(value) {
    return String(Math.round(value * 100) / 100);
  }

  /**
   * Remove every listener.
   */
  dispose() {
    this.#shown.dispose();
  }
}

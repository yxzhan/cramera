/**
 * Building a part's elements and owning its listeners, so the part removes every
 * listener it added when it is disposed.
 */

/**
 * The names of the builder's controls, kept in `data-action`, so a control can be
 * found by what it does.
 */
export const PlanAction = Object.freeze({
  NEW: 'new',
  OPEN: 'open',
  SAVE: 'save',
  SHOW_DEMO: 'show-demo',
  WRITE_DEMO: 'write-demo',
  STAGE: 'stage',
  PERFORM: 'perform',
  SIMULATE: 'simulate',
  VALIDATE: 'validate',
  RUN_ON_ROBOT: 'run-on-robot',
  STOP: 'stop',
  ACKNOWLEDGE_STOP: 'acknowledge-stop',
  RESTART_PLAN: 'restart-plan',
  ABORT_PLAN: 'abort-plan',
  CAPTURE: 'capture',
  NAME: 'name',
  TASK: 'task',
  ENVIRONMENT: 'environment',
  URDF_PATH: 'urdf-path',
  LOAD_URDF: 'load-urdf',
  BROWSE_URDF: 'browse-urdf',
  OPEN_FOLDER: 'open-folder',
  FOLDER_UP: 'folder-up',
  PICK_URDF: 'pick-urdf',
  CLOSE_PICKER: 'close-picker',
  SELECT_ROBOT: 'select-robot',
  ADD_ROBOT: 'add-robot',
  REMOVE_ROBOT: 'remove-robot',
  ROBOT_MODEL: 'robot-model',
  ROBOT_LABEL: 'robot-label',
  POSE: 'pose',
  ADD_STEP: 'add-step',
  ADD_SKILL: 'add-skill',
  DEVELOPER_MODE: 'developer-mode',
  NEW_FROM_TEMPLATE: 'new-from-template',
  REMOVE_STEP: 'remove-step',
  MOVE_STEP_UP: 'move-step-up',
  MOVE_STEP_DOWN: 'move-step-down',
  STEP_PARAMETER: 'step-parameter',
  USE_LIVE_POSE: 'use-live-pose',
  ADD_OBJECT: 'add-object',
  SCAN_SCENE: 'scan-scene',
  OBJECT_MODEL: 'object-model',
  SELECT_OBJECT: 'select-object',
  REMOVE_OBJECT: 'remove-object',
  CONSTRAINT_TEXT: 'constraint-text',
  ADD_CONSTRAINT: 'add-constraint',
  REMOVE_CONSTRAINT: 'remove-constraint',
  ATTACH_CONSTRAINT: 'attach-constraint',
  DETACH_CONSTRAINT: 'detach-constraint',
  TURN: 'turn',
  FRAME: 'frame',
  TOGGLE_RUN: 'toggle-run',
  REPLACE_PLAN: 'replace-plan',
  KEEP_PLAN: 'keep-plan',
  SKIP_TO_FLOW: 'skip-to-flow',
});

/**
 * The DOM events the builder listens to.
 */
export const DomEvent = Object.freeze({
  CLICK: 'click',
  CHANGE: 'change',
  KEY_DOWN: 'keydown',
  MOUSE_DOWN: 'mousedown',
  BLUR: 'blur',
  DRAG_START: 'dragstart',
  DRAG_OVER: 'dragover',
  DRAG_LEAVE: 'dragleave',
  DROP: 'drop',
  TOGGLE: 'toggle',
});

/**
 * What a dragged palette entry carries, as `<kind>:<value>` in the drag's text.
 */
export const DragKind = Object.freeze({ STEP: 'step', SKILL: 'skill', CONSTRAINT: 'constraint' });

/**
 * The media type of a drag's text.
 */
export const DRAG_MEDIA_TYPE = 'text/plain';

/**
 * Separates a drag's kind from its value.
 */
const DRAG_SEPARATOR = ':';

// %% drags

export class DragText {
  /**
   * @param {string} kind A `DragKind` value.
   * @param {string} value What is dragged.
   * @returns {string} The drag's text.
   */
  static of(kind, value) {
    return `${kind}${DRAG_SEPARATOR}${value}`;
  }

  /**
   * @param {string} text A drag's text.
   * @param {string} kind A `DragKind` value.
   * @returns {string|null} The dragged value when the drag is of that kind, else null.
   */
  static valueOf(text, kind) {
    const prefix = `${kind}${DRAG_SEPARATOR}`;
    return text.startsWith(prefix) ? text.slice(prefix.length) : null;
  }
}

// %% elements

export class Elements {
  /**
   * The document the elements belong to.
   * @type {Document}
   */
  #document;

  /**
   * Every listener added, to remove them again.
   * @type {Array<[EventTarget, string, function(Event): void]>}
   */
  #listeners = [];

  /**
   * @param {Document} document The document the elements belong to.
   */
  constructor(document) {
    this.#document = document;
  }

  /**
   * @returns {Document} The document the elements belong to.
   */
  get document() {
    return this.#document;
  }

  /**
   * @param {string} tag The element's tag.
   * @param {string=} className Its classes.
   * @param {string=} text Its text, inserted as text, never as HTML.
   * @returns {HTMLElement} The element.
   */
  create(tag, className = '', text = '') {
    const element = this.#document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  /**
   * @param {string} text The button's text.
   * @param {string} action Its `PlanAction` value.
   * @param {function(): void} onClick Called on every click.
   * @returns {HTMLButtonElement} The button.
   */
  button(text, action, onClick) {
    const button = this.create('button', '', text);
    button.type = 'button';
    button.dataset.action = action;
    this.listen(button, DomEvent.CLICK, onClick);
    return button;
  }

  /**
   * @param {string} type The input's type.
   * @param {string} action Its `PlanAction` value.
   * @param {function(): void} onChange Called when the person commits a change.
   * @returns {HTMLInputElement} The input.
   */
  input(type, action, onChange) {
    const input = this.create('input');
    input.type = type;
    input.dataset.action = action;
    this.listen(input, DomEvent.CHANGE, onChange);
    return input;
  }

  /**
   * @param {string} action The select's `PlanAction` value.
   * @param {Array<{value: string, label: string}>} options The options, in order.
   * @param {string} value The value selected, if an option has it.
   * @param {function(string): void} onChange Called with the picked value.
   * @returns {HTMLSelectElement} The select.
   */
  select(action, options, value, onChange) {
    const select = this.create('select');
    select.dataset.action = action;
    this.options(select, options, value);
    this.listen(select, DomEvent.CHANGE, () => onChange(select.value));
    return select;
  }

  /**
   * Replace a select's options.
   * @param {HTMLSelectElement} select The select.
   * @param {Array<{value: string, label: string}>} options The options, in order.
   * @param {string} value The value to keep selected, if an option has it.
   */
  options(select, options, value) {
    select.replaceChildren(
      ...options.map((option) => {
        const element = this.create('option', '', option.label);
        element.value = option.value;
        return element;
      }),
    );
    select.value = options.some((option) => option.value === value) ? value : (options[0]?.value ?? '');
  }

  /**
   * @param {string} text The label's text.
   * @param {HTMLElement} control The labelled control.
   * @param {string=} className The label's classes.
   * @returns {HTMLLabelElement} The label holding its text and the control.
   */
  label(text, control, className = '') {
    const label = this.create('label', className);
    label.append(this.create('span', '', text), control);
    return label;
  }

  /**
   * @param {string} text The input's label.
   * @param {number} value The number shown.
   * @param {string} action Its `PlanAction` value.
   * @param {function(number): void} onChange Called with a finite committed number.
   * @returns {HTMLLabelElement} The labelled number input.
   */
  number(text, value, action, onChange) {
    const input = this.input('number', action, () => {
      const number = Number(input.value);
      if (input.value !== '' && Number.isFinite(number)) onChange(number);
    });
    input.value = String(value);
    input.step = 'any';
    input.dataset.field = text;
    return this.label(text, input, 'number');
  }

  /**
   * @param {HTMLElement} element An element a palette entry can be dragged from.
   * @param {string} text The drag's text, see `DragText`.
   */
  draggable(element, text) {
    element.draggable = true;
    this.listen(element, DomEvent.DRAG_START, (event) => event.dataTransfer?.setData(DRAG_MEDIA_TYPE, text));
  }

  /**
   * @param {HTMLElement} element An element palette entries can be dropped on.
   * @param {string} highlight The class shown while a drag is over it.
   * @param {function(string, Event): void} onDrop Called with the drag's text.
   */
  dropTarget(element, highlight, onDrop) {
    this.listen(element, DomEvent.DRAG_OVER, (event) => {
      event.preventDefault();
      element.classList.add(highlight);
    });
    this.listen(element, DomEvent.DRAG_LEAVE, () => element.classList.remove(highlight));
    this.listen(element, DomEvent.DROP, (event) => {
      event.preventDefault();
      event.stopPropagation?.();
      element.classList.remove(highlight);
      onDrop(event.dataTransfer?.getData(DRAG_MEDIA_TYPE) ?? '', event);
    });
  }

  /**
   * @param {EventTarget} target What to listen on.
   * @param {string} type The event type.
   * @param {function(Event): void} listener The listener.
   */
  listen(target, type, listener) {
    target.addEventListener(type, listener);
    this.#listeners.push([target, type, listener]);
  }

  /**
   * Remove every listener added.
   */
  dispose() {
    for (const [target, type, listener] of this.#listeners) target.removeEventListener(type, listener);
    this.#listeners = [];
  }
}

/**
 * The edited robot's plan: the palette of the actions the CRAM workspace offers, grouped
 * as the catalog groups them, and the steps with forms built from their actions' fields,
 * their constraints, live state and validation; actions are dragged or clicked into the
 * plan, constraints dragged onto a step.
 */

import {
  ActionFieldKey,
  ActionFieldKind,
  ActionSchemaKey,
  DestinationKey,
  DetectionRegionKey,
  FailedActionModuleKey,
  PlacementMode,
  PlanBuilderCatalogKey,
  PlannedObjectKey,
  PlanStepKey,
  RegionKind,
  SkillKey,
  StepConstraintKey,
  StepFailureReason,
  StepValidationKey,
  StepVerdict,
  ZoneKey,
} from '../../../protocol.js';
import { DomEvent, DragKind, DragText, Elements, PlanAction } from './elements.js';
import { PlanState } from './plan-state.js';
import { PlanPose } from './poses.js';
import { STEP_PALETTE_SIZE, StepCard } from './step-card.js';

export { STEP_PALETTE_SIZE };

/**
 * How people read each way of putting an object down.
 */
const PLACEMENT_LABELS = Object.freeze({
  [PlacementMode.POSE]: 'at a pose',
  [PlacementMode.SURFACE]: 'on a surface',
  [PlacementMode.ZONE]: 'in a zone',
  [PlacementMode.ON_TOP_OF]: 'on top of',
  [PlacementMode.MARKER]: 'at a marker',
});

/**
 * The surface choice that lets the step take any surface of its type.
 */
const ANY_SURFACE = Object.freeze({ value: '', label: 'any of the type' });

/**
 * How people read each region a step looks in.
 */
const REGION_LABELS = Object.freeze({
  [RegionKind.TABLE]: 'whole table',
  [RegionKind.ZONE]: 'a zone',
  [RegionKind.MARKER]: 'around a marker',
});

/**
 * What a step's validation says of a step that did not fail, by its verdict.
 */
export const VERDICT_TEXTS = Object.freeze({
  [StepVerdict.VALID]: 'Valid.',
  [StepVerdict.ASSUMED]: 'Assumed from the twin, since the camera does not see it now; perceived again when the plan runs.',
  [StepVerdict.UNCHECKED]: 'Not checked, since an earlier step fails.',
});

/**
 * Why a step failed its validation, as people read it, by the reason.
 */
export const REASON_TEXTS = Object.freeze({
  [StepFailureReason.UNREACHABLE]: 'unreachable: the robot cannot reach where the step asks',
  [StepFailureReason.COLLISION]: 'collision: the motion would collide',
  [StepFailureReason.NOT_PERCEIVED]: 'not perceived: nothing matching was seen in the region',
  [StepFailureReason.AMBIGUOUS]: 'ambiguous: several objects match and nothing tells them apart',
  [StepFailureReason.SAFETY_LIMIT]: 'outside the workspace: an arm stands outside, or would leave, the box the safety monitor holds it to; the step says which arm and what to do',
  [StepFailureReason.SKILL_NOT_OFFERED]: 'the robot does not offer this skill',
  [StepFailureReason.UNSUCCESSFUL]: 'the step did not succeed',
});

/**
 * @param {Object} validation A `StepValidation`.
 * @returns {Array<string>} The lines of what a failed step's validation found when its
 *   reason names no kind of failure, the found reason first; none otherwise.
 */
function foundReason(validation) {
  const generic = [undefined, StepFailureReason.UNSUCCESSFUL].includes(validation[StepValidationKey.REASON]);
  const detail = validation[StepValidationKey.DETAIL] ?? '';
  return validation[StepValidationKey.VERDICT] === StepVerdict.FAILED && generic && detail ? detail.split('\n') : [];
}

/**
 * @param {Object} validation A `StepValidation`.
 * @returns {string} What the step's card says of how the step fared, with the arm it
 *   was given when the arm was chosen automatically. A step that did not succeed leads
 *   with the reason its validation found; the general words stand only without one.
 */
export function validationText(validation) {
  const verdict = validation[StepValidationKey.VERDICT];
  const reason = validation[StepValidationKey.REASON];
  const [found] = foundReason(validation);
  const failure = found ? `Fails: ${found}` : `Fails: ${REASON_TEXTS[reason] ?? reason}.`;
  const text = verdict === StepVerdict.FAILED ? failure : VERDICT_TEXTS[verdict];
  const arm = validation[StepValidationKey.ARM];
  return arm ? `${text} Arm: ${arm}, chosen automatically.` : text;
}

/**
 * @param {Object} validation A `StepValidation`.
 * @returns {string} What a failed step's card shows below its validation line: what the
 *   validation found and suggests, without the reason the line already leads with.
 */
function validationDetail(validation) {
  if (validation[StepValidationKey.VERDICT] !== StepVerdict.FAILED) return '';
  const found = foundReason(validation);
  return found.length > 0 ? found.slice(1).join('\n') : (validation[StepValidationKey.DETAIL] ?? '');
}

/**
 * The choice of an optional field that leaves the action's default.
 */
const DEFAULT_CHOICE = '';

/**
 * @param {string} annotation An annotation class's identifier.
 * @returns {string} What an annotation field says when nothing carries the annotation.
 */
export const noAnnotation = (annotation) => `Neither the environment nor the live world shows an entity with a ${annotation} annotation; type its name.`;

/**
 * The classes the view's elements carry.
 */
const ViewClass = Object.freeze({
  PALETTE: 'palette',
  GROUP: 'palette-group',
  BLOCK: 'block',
  UNAVAILABLE: 'unavailable',
  NOTICE: 'notice',
  LIST: 'steps',
  PARAMETERS: 'step-parameters',
  MORE: 'more-options',
  PROBLEM: 'step-problem',
  VALIDATION: 'step-validation',
  VALIDATION_DETAIL: 'step-validation-detail',
  DERIVED: 'derived',
  CHIPS: 'chips',
  CHIP: 'chip',
  DROP: 'drop-hint',
  DRAG_OVER: 'drag-over',
  EMPTY: 'empty',
});

// %% view

export class StepsView {
  /**
   * The palette of actions.
   * @type {HTMLElement}
   */
  palette;

  /**
   * The list of steps.
   * @type {HTMLElement}
   */
  list;

  /**
   * The page that applies the person's edits.
   * @type {Object}
   */
  #editor;

  /**
   * Owns the listeners of the list's frame.
   * @type {Elements}
   */
  #frame;

  /**
   * Owns the listeners of the shown palette and steps, replaced on every render.
   * @type {Elements}
   */
  #shown;

  /**
   * The folded parts the person unfolded, kept unfolded across renders: the steps
   * whose optional fields are shown, by identity, and the list of actions the robot
   * cannot take.
   * @type {Set<string>}
   */
  #unfolded = new Set();

  /**
   * @param {Document} document The page's document.
   * @param {Object} editor The page: `state` (the `PlanState`), `edit(change)` (applies
   *   a change of the plan and shows it), `attachConstraint(stepIdentifier, text)`,
   *   `useLivePose(stepIdentifier, field)` and `surfaces()` (the live world's
   *   annotation names by type).
   */
  constructor(document, editor) {
    this.#editor = editor;
    this.#frame = new Elements(document);
    this.#shown = new Elements(document);
    this.palette = this.#frame.create('div', ViewClass.PALETTE);
    this.list = this.#frame.create('ol', ViewClass.LIST);
    this.#frame.dropTarget(this.list, ViewClass.DRAG_OVER, (text) => this.#dropped(text, this.#editor.state.steps().length));
  }

  /**
   * Show the edited robot's palette and steps.
   * @param {Array<string|null>} statuses The live status of each step, by position.
   * @param {Array<Map<string, string|null>>} observations What each step's constraints
   *   observe in the live plan, by their text, by position.
   * @param {Map<string, Object>} validations How each step fared in the plan's current
   *   validation, as a `StepValidation`, by the step's identity; empty while the plan
   *   as it stands is not validated.
   */
  render(statuses = [], observations = [], validations = new Map()) {
    this.#shown.dispose();
    this.#shown = new Elements(this.#frame.document);
    this.palette.replaceChildren(...this.#paletteEntries());
    const steps = this.#editor.state.steps();
    if (steps.length === 0) {
      this.list.replaceChildren(this.#shown.create('li', ViewClass.DROP, 'Drag actions here, or click them, to build the plan.'));
      return;
    }
    this.list.replaceChildren(...steps.map((step, index) => this.#card(step, index, statuses[index] ?? null, observations[index] ?? new Map(), validations.get(step[PlanStepKey.IDENTIFIER]) ?? null)));
  }

  // %% palette

  /**
   * @returns {Array<HTMLElement>} The offered actions by group, the actions the robot
   *   cannot take with why, and the action modules that failed to import.
   */
  #paletteEntries() {
    const elements = this.#shown;
    const state = this.#editor.state;
    const robot = state.activeRobot();
    const entries = [];
    const failed = state.catalog[PlanBuilderCatalogKey.FAILED_ACTION_MODULES] ?? [];
    if (failed.length > 0) {
      const notice = elements.create('p', ViewClass.NOTICE, `${failed.length} action module${failed.length === 1 ? '' : 's'} failed to import, so their actions are missing: ${failed.map((module) => module[FailedActionModuleKey.MODULE]).join(', ')}.`);
      notice.title = failed.map((module) => `${module[FailedActionModuleKey.MODULE]}: ${module[FailedActionModuleKey.ERROR]}`).join('\n');
      entries.push(notice);
    }
    if (!robot) return entries;
    if (state.curated()) {
      const section = elements.create('div', ViewClass.GROUP);
      section.dataset.group = 'skills';
      section.append(elements.create('h3', '', 'Skills'), ...state.offeredSkills(robot).map((skill) => this.#skillBlock(skill)));
      entries.push(section);
      return entries;
    }
    const groups = new Map();
    const refused = [];
    for (const schema of state.actions()) {
      const refusal = state.refusal(robot, schema);
      if (refusal !== null) {
        refused.push([schema, refusal]);
        continue;
      }
      const group = schema[ActionSchemaKey.GROUP];
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(this.#block(schema));
    }
    for (const [group, blocks] of groups) {
      const section = elements.create('div', ViewClass.GROUP);
      section.dataset.group = group;
      section.append(elements.create('h3', '', group), ...blocks);
      entries.push(section);
    }
    if (refused.length > 0) entries.push(this.#refused(refused));
    return entries;
  }

  /**
   * @param {Object} schema An action the robot can take.
   * @returns {HTMLElement} Its palette entry, clicked or dragged into the plan.
   */
  #block(schema) {
    const name = schema[ActionSchemaKey.NAME];
    const block = this.#shown.button(schema[ActionSchemaKey.LABEL], PlanAction.ADD_STEP, () => this.#add(name, this.#editor.state.steps().length));
    block.className = ViewClass.BLOCK;
    block.dataset.name = name;
    block.dataset.palette = String(this.#paletteIndex(name));
    block.title = schema[ActionSchemaKey.DOCUMENTATION] || name;
    this.#shown.draggable(block, DragText.of(DragKind.STEP, name));
    return block;
  }

  /**
   * @param {Object} skill A skill of the robot profile the robot can take.
   * @returns {HTMLElement} Its palette entry, clicked or dragged into the plan.
   */
  #skillBlock(skill) {
    const identifier = skill[SkillKey.IDENTIFIER];
    const action = skill[SkillKey.ACTION];
    const block = this.#shown.button(skill[SkillKey.LABEL], PlanAction.ADD_SKILL, () => this.#addSkill(identifier, this.#editor.state.steps().length));
    block.className = ViewClass.BLOCK;
    block.dataset.skill = identifier;
    block.dataset.palette = String(this.#paletteIndex(action));
    block.title = skill[SkillKey.DOCUMENTATION] || skill[SkillKey.LABEL];
    this.#shown.draggable(block, DragText.of(DragKind.SKILL, identifier));
    return block;
  }

  /**
   * @param {Array<[Object, string]>} refused The actions the robot cannot take, with why.
   * @returns {HTMLElement} A folded list of them, greyed, each with its reason.
   */
  #refused(refused) {
    const elements = this.#shown;
    const details = this.#foldable(ViewClass.UNAVAILABLE, ViewClass.UNAVAILABLE);
    details.append(elements.create('summary', '', `Not available (${refused.length})`));
    const list = elements.create('ul');
    for (const [schema, refusal] of refused) {
      const item = elements.create('li', `${ViewClass.BLOCK} ${ViewClass.UNAVAILABLE}`);
      item.dataset.name = schema[ActionSchemaKey.NAME];
      item.title = schema[ActionSchemaKey.DOCUMENTATION] || schema[ActionSchemaKey.NAME];
      item.append(elements.create('strong', '', schema[ActionSchemaKey.LABEL]), elements.create('span', 'reason', refusal));
      list.append(item);
    }
    details.append(list);
    return details;
  }

  /**
   * @param {string} name An action's class name.
   * @returns {number} The colour of its steps: its position in the catalog, wrapped.
   */
  #paletteIndex(name) {
    const index = this.#editor.state.actions().findIndex((schema) => schema[ActionSchemaKey.NAME] === name);
    return Math.max(index, 0) % STEP_PALETTE_SIZE;
  }

  // %% steps

  /**
   * @param {Object} step A step of the edited robot.
   * @param {number} index Its position.
   * @param {string|null} status Its live status, or null.
   * @param {Map<string, string|null>} observations What its constraints observe live.
   * @param {Object|null} validation How it fared in the plan's current validation, or
   *   null.
   * @returns {HTMLElement} The step's card.
   */
  #card(step, index, status, observations, validation) {
    const elements = this.#shown;
    const identifier = step[PlanStepKey.IDENTIFIER];
    const action = step[PlanStepKey.ACTION];
    const schema = this.#editor.state.schema(action);
    const view = new StepCard(elements, index + 1, this.#editor.state.stepLabel(step), schema?.[ActionSchemaKey.DOCUMENTATION] || action, this.#paletteIndex(action));
    const card = view.card;
    card.dataset.step = identifier;
    if (status) view.showStatus(status);
    const controls = elements.create('span', 'step-controls');
    controls.append(
      elements.button('↑', PlanAction.MOVE_STEP_UP, () => this.#edit(() => this.#editor.state.moveStep(identifier, -1))),
      elements.button('↓', PlanAction.MOVE_STEP_DOWN, () => this.#edit(() => this.#editor.state.moveStep(identifier, 1))),
      elements.button('×', PlanAction.REMOVE_STEP, () => this.#edit(() => this.#editor.state.removeStep(identifier))),
    );
    view.head.append(controls);
    for (const problem of this.#editor.state.problems(step)) card.append(elements.create('p', ViewClass.PROBLEM, problem));
    if (validation) {
      card.dataset.validation = validation[StepValidationKey.VERDICT];
      const line = elements.create('p', ViewClass.VALIDATION, validationText(validation));
      line.title = validation[StepValidationKey.DETAIL] ?? '';
      card.append(line);
      // What a failed step's validation found, and what it suggests, is what the person
      // acts on: shown on the card, not only on hover.
      const detail = validationDetail(validation);
      if (detail) card.append(elements.create('p', ViewClass.VALIDATION_DETAIL, detail));
    }
    if (schema) card.append(...this.#form(step, schema));
    card.append(this.#chips(step, observations));
    elements.dropTarget(card, ViewClass.DRAG_OVER, (text) => this.#dropped(text, index, identifier));
    return card;
  }

  /**
   * @param {Object} step A step of the edited robot.
   * @param {Object} schema Its action.
   * @returns {Array<HTMLElement>} The controls of the required fields, and the optional
   *   ones folded under "More options".
   */
  #form(step, schema) {
    const elements = this.#shown;
    const required = elements.create('div', ViewClass.PARAMETERS);
    const optional = elements.create('div', ViewClass.PARAMETERS);
    for (const field of this.#editor.state.shownFields(step, schema)) {
      const box = field[ActionFieldKey.REQUIRED] ? required : optional;
      box.append(...this.#control(step, field));
    }
    if (optional.children.length === 0) return [required];
    const more = this.#foldable(ViewClass.MORE, step[PlanStepKey.IDENTIFIER]);
    more.append(elements.create('summary', '', 'More options'), optional);
    return [required, more];
  }

  /**
   * @param {string} className The folded part's class.
   * @param {string} key What the part is remembered by across renders.
   * @returns {HTMLDetailsElement} A folded part, unfolded when the person left it so.
   */
  #foldable(className, key) {
    const details = this.#shown.create('details', className);
    details.open = this.#unfolded.has(key);
    this.#shown.listen(details, DomEvent.TOGGLE, () => {
      if (details.open) this.#unfolded.add(key);
      else this.#unfolded.delete(key);
    });
    return details;
  }

  /**
   * @param {Object} step A step of the edited robot.
   * @param {Object} field A field of its action.
   * @returns {Array<HTMLElement>} The field's controls, by its kind.
   */
  #control(step, field) {
    const controls = {
      [ActionFieldKind.BODY]: () => this.#objectPicker(step, field),
      [ActionFieldKind.ANNOTATION]: () => this.#annotationPicker(step, field),
      [ActionFieldKind.ENUM]: () => this.#memberPicker(step, field),
      [ActionFieldKind.POSE]: () => (field[ActionFieldKey.ACCEPTS_SURFACE] ? this.#destination(step, field) : this.#poseEditor(step, field)),
      [ActionFieldKind.POSE_OR_SURFACE]: () => this.#destination(step, field),
      [ActionFieldKind.NUMBER]: () => this.#numberInput(step, field),
      [ActionFieldKind.INTEGER]: () => this.#numberInput(step, field),
      [ActionFieldKind.BOOLEAN]: () => this.#checkbox(step, field),
      [ActionFieldKind.TEXT]: () => this.#textInput(step, field),
      [ActionFieldKind.GRASP]: () => this.#derivedNote(field),
      [ActionFieldKind.REGION]: () => this.#regionPicker(step, field),
    };
    return (controls[field[ActionFieldKey.KIND]] ?? (() => []))();
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A field of its action.
   * @param {HTMLElement} control The field's control.
   * @returns {HTMLLabelElement} The control labelled with the field's name, its meaning
   *   as tooltip.
   */
  #labelled(step, field, control) {
    control.dataset.field = field[ActionFieldKey.NAME];
    const label = this.#shown.label(field[ActionFieldKey.LABEL], control);
    label.title = field[ActionFieldKey.DOCUMENTATION] || field[ActionFieldKey.NAME];
    return label;
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field One of its fields.
   * @param {*} value The field's new value; null leaves the action's default.
   */
  #set(step, field, value) {
    this.#edit(() => this.#editor.state.setValue(step[PlanStepKey.IDENTIFIER], field[ActionFieldKey.NAME], value));
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field Its field.
   * @returns {*} The field's value, or undefined.
   */
  static #value(step, field) {
    return step[PlanStepKey.VALUES][field[ActionFieldKey.NAME]];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A body field.
   * @returns {Array<HTMLElement>} A choice of the plan's objects and of the objects
   *   earlier steps find.
   */
  #objectPicker(step, field) {
    const objects = this.#editor.state.objectChoices(step[PlanStepKey.IDENTIFIER]);
    if (objects.length === 0) return [this.#shown.create('p', ViewClass.EMPTY, `Add an object for ${field[ActionFieldKey.LABEL]}.`)];
    const value = StepsView.#value(step, field);
    // A required object without a value says so: a select would otherwise show its first
    // object as if it were chosen.
    const first = !field[ActionFieldKey.REQUIRED] ? [{ value: DEFAULT_CHOICE, label: 'none' }] : value == null ? [{ value: DEFAULT_CHOICE, label: 'choose…' }] : [];
    const select = this.#shown.select(PlanAction.STEP_PARAMETER, [...first, ...objects], value ?? DEFAULT_CHOICE, (picked) => this.#set(step, field, picked || null));
    return [this.#labelled(step, field, select)];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A region field.
   * @returns {Array<HTMLElement>} A choice of the regions the step can look in.
   */
  #regionPicker(step, field) {
    const state = this.#editor.state;
    const identifier = step[PlanStepKey.IDENTIFIER];
    const name = field[ActionFieldKey.NAME];
    const kinds = Object.values(RegionKind).filter((kind) => kind !== RegionKind.ZONE || state.zones().length > 0);
    const regions = kinds.map((kind) => ({ value: kind, label: REGION_LABELS[kind] ?? kind }));
    const region = StepsView.#value(step, field) ?? {};
    const current = region[DetectionRegionKey.KIND] ?? RegionKind.TABLE;
    const select = this.#shown.select(PlanAction.STEP_PARAMETER, regions, current, (kind) => this.#edit(() => state.setRegion(identifier, name, kind)));
    const controls = [this.#labelled(step, field, select)];
    if (current === RegionKind.ZONE) controls.push(this.#zonePicker(region[DetectionRegionKey.ZONE], DetectionRegionKey.ZONE, (zone) => this.#set(step, field, { ...region, [DetectionRegionKey.ZONE]: zone })));
    if (current === RegionKind.MARKER) controls.push(this.#markerRow(region[DetectionRegionKey.POSE], (pose) => this.#edit(() => state.setTarget(identifier, name, pose))));
    return controls;
  }

  /**
   * @param {string|undefined} zone The chosen zone's identifier.
   * @param {string} key The key the choice is written under.
   * @param {function(string): void} onChange Called with the zone chosen.
   * @returns {HTMLElement} A choice of the robot profile's zones.
   */
  #zonePicker(zone, key, onChange) {
    const zones = this.#editor.state.zones().map((entry) => ({ value: entry[ZoneKey.IDENTIFIER], label: entry[ZoneKey.LABEL] }));
    const select = this.#shown.select(PlanAction.STEP_PARAMETER, zones, zone ?? zones[0]?.value, onChange);
    select.dataset.field = key;
    return this.#shown.label('zone', select);
  }

  /**
   * @param {Array<number>|undefined} pose A marker's pose.
   * @param {function(Array<number>): void} onChange Called with the edited pose, before
   *   it rests on the surface below it.
   * @returns {HTMLElement} The controls of the marker's position on the surface and its
   *   heading; its height follows the surface.
   */
  #markerRow(pose, onChange) {
    const elements = this.#shown;
    const target = pose ?? PlanPose.of(0, 0, 0, 0);
    const position = PlanPose.position(target);
    const row = elements.create('div', 'pose-row');
    row.dataset.marker = 'true';
    const coordinates = ['x', 'y'].map((axis, index) =>
      elements.number(axis, position[index], PlanAction.POSE, (value) => {
        const moved = [...position];
        moved[index] = value;
        onChange(PlanPose.moved(target, moved));
      }),
    );
    const heading = elements.number('yaw°', PlanPose.degrees(PlanPose.yaw(target)), PlanAction.POSE, (value) => onChange(PlanPose.turned(target, PlanPose.radians(value))));
    row.append(...coordinates, heading);
    return row;
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field An annotation field.
   * @returns {Array<HTMLElement>} A choice of the environment's and the live world's
   *   entities carrying the annotation, or a note that they hold none and a text input
   *   for the name.
   */
  #annotationPicker(step, field) {
    const elements = this.#shown;
    const annotation = field[ActionFieldKey.ANNOTATION];
    const value = StepsView.#value(step, field) ?? '';
    const known = this.#editor.surfaces().get(annotation) ?? [];
    if (known.length > 0) {
      const choices = [{ value: DEFAULT_CHOICE, label: 'choose…' }, ...known.map((name) => ({ value: name, label: name }))];
      return [this.#labelled(step, field, elements.select(PlanAction.STEP_PARAMETER, choices, value, (picked) => this.#set(step, field, picked || null)))];
    }
    const input = elements.input('text', PlanAction.STEP_PARAMETER, () => this.#set(step, field, input.value.trim() || null));
    input.value = value;
    input.placeholder = `${annotation} name`;
    return [this.#labelled(step, field, input), elements.create('p', ViewClass.EMPTY, noAnnotation(annotation))];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field An enumeration field.
   * @returns {Array<HTMLElement>} A choice of the members the robot offers.
   */
  #memberPicker(step, field) {
    const state = this.#editor.state;
    const members = state.choices(state.activeRobot(), field).map((member) => ({ value: member, label: member }));
    const fallback = field[ActionFieldKey.DEFAULT] ? `default (${field[ActionFieldKey.DEFAULT]})` : 'default';
    const choices = field[ActionFieldKey.REQUIRED] ? members : [{ value: DEFAULT_CHOICE, label: fallback }, ...members];
    const select = this.#shown.select(PlanAction.STEP_PARAMETER, choices, StepsView.#value(step, field) ?? DEFAULT_CHOICE, (value) => this.#set(step, field, value || null));
    return [this.#labelled(step, field, select)];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A number or integer field.
   * @returns {Array<HTMLElement>} A number input; empty leaves the default, shown as
   *   placeholder.
   */
  #numberInput(step, field) {
    const input = this.#shown.input('number', PlanAction.STEP_PARAMETER, () => {
      const number = Number(input.value);
      if (input.value === '') this.#set(step, field, null);
      else if (Number.isFinite(number)) this.#set(step, field, field[ActionFieldKey.KIND] === ActionFieldKind.INTEGER ? Math.round(number) : number);
    });
    const value = StepsView.#value(step, field);
    input.value = value === undefined ? '' : String(value);
    input.step = field[ActionFieldKey.KIND] === ActionFieldKind.INTEGER ? '1' : 'any';
    input.placeholder = field[ActionFieldKey.DEFAULT] === undefined || field[ActionFieldKey.DEFAULT] === null ? 'default' : String(field[ActionFieldKey.DEFAULT]);
    return [this.#labelled(step, field, input)];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A boolean field.
   * @returns {Array<HTMLElement>} A checkbox, showing the default until it is changed.
   */
  #checkbox(step, field) {
    const input = this.#shown.input('checkbox', PlanAction.STEP_PARAMETER, () => this.#set(step, field, input.checked));
    input.checked = Boolean(StepsView.#value(step, field) ?? field[ActionFieldKey.DEFAULT]);
    return [this.#labelled(step, field, input)];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A text field.
   * @returns {Array<HTMLElement>} A text input; empty leaves the default.
   */
  #textInput(step, field) {
    const input = this.#shown.input('text', PlanAction.STEP_PARAMETER, () => this.#set(step, field, input.value.trim() || null));
    input.value = StepsView.#value(step, field) ?? '';
    input.placeholder = field[ActionFieldKey.DEFAULT] ?? 'default';
    return [this.#labelled(step, field, input)];
  }

  /**
   * @param {Object} field A grasp field.
   * @returns {Array<HTMLElement>} A note that the grasp is derived, never entered.
   */
  #derivedNote(field) {
    const sources = (field[ActionFieldKey.DERIVED_FROM] ?? []).join(' and ');
    const text = field[ActionFieldKey.REQUIRED] ? `${field[ActionFieldKey.LABEL]}: derived from ${sources} when the step runs.` : `${field[ActionFieldKey.LABEL]}: left to the action.`;
    const note = this.#shown.create('p', ViewClass.DERIVED, text);
    note.dataset.field = field[ActionFieldKey.NAME];
    return [note];
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A field accepting a surface.
   * @returns {Array<HTMLElement>} The choice of a pose, a surface, a zone, an object to
   *   put it on top of or a marker, and its controls.
   */
  #destination(step, field) {
    const elements = this.#shown;
    const state = this.#editor.state;
    const identifier = step[PlanStepKey.IDENTIFIER];
    const name = field[ActionFieldKey.NAME];
    const destination = StepsView.#value(step, field) ?? {};
    const placement = destination[DestinationKey.PLACEMENT] ?? PlacementMode.POSE;
    const offered = Object.values(PlacementMode).filter((mode) => mode !== PlacementMode.ZONE || state.zones().length > 0);
    const modes = offered.map((mode) => ({ value: mode, label: PLACEMENT_LABELS[mode] }));
    const controls = [this.#labelled(step, field, elements.select(PlanAction.STEP_PARAMETER, modes, placement, (value) => this.#edit(() => state.setPlacement(identifier, name, value))))];
    const update = (changes) => this.#set(step, field, { ...destination, ...changes });
    if (placement === PlacementMode.POSE) return [...controls, this.#poseRow(destination[DestinationKey.POSE], (pose) => update({ [DestinationKey.POSE]: pose }))];
    if (placement === PlacementMode.MARKER) return [...controls, this.#markerRow(destination[DestinationKey.POSE], (pose) => this.#edit(() => state.setTarget(identifier, name, pose)))];
    if (placement === PlacementMode.ZONE) return [...controls, this.#zonePicker(destination[DestinationKey.ZONE], DestinationKey.ZONE, (zone) => update({ [DestinationKey.ZONE]: zone }))];
    if (placement === PlacementMode.ON_TOP_OF) {
      const supports = state.supportChoices(identifier);
      const select = elements.select(PlanAction.STEP_PARAMETER, supports, destination[DestinationKey.OBJECT] ?? supports[0]?.value, (value) => update({ [DestinationKey.OBJECT]: value }));
      select.dataset.field = DestinationKey.OBJECT;
      return [...controls, elements.label('object', select)];
    }
    const types = state.catalog[PlanBuilderCatalogKey.SURFACE_TYPES].map((type) => ({ value: type, label: type }));
    const surfaceType = destination[DestinationKey.SURFACE_TYPE];
    const named = (this.#editor.surfaces().get(surfaceType) ?? []).map((surface) => ({ value: surface, label: surface }));
    const typeSelect = elements.select(PlanAction.STEP_PARAMETER, types, surfaceType, (value) => update({ [DestinationKey.SURFACE_TYPE]: value, [DestinationKey.SURFACE_NAME]: undefined }));
    typeSelect.dataset.field = DestinationKey.SURFACE_TYPE;
    const nameSelect = elements.select(PlanAction.STEP_PARAMETER, [ANY_SURFACE, ...named], destination[DestinationKey.SURFACE_NAME] ?? ANY_SURFACE.value, (value) => update({ [DestinationKey.SURFACE_NAME]: value || undefined }));
    nameSelect.dataset.field = DestinationKey.SURFACE_NAME;
    controls.push(elements.label('surface', typeSelect), elements.label('which', nameSelect));
    return controls;
  }

  /**
   * @param {Object} step A step.
   * @param {Object} field A pose field.
   * @returns {Array<HTMLElement>} The pose's controls and a button taking the robot's
   *   live pose.
   */
  #poseEditor(step, field) {
    const elements = this.#shown;
    const identifier = step[PlanStepKey.IDENTIFIER];
    const name = field[ActionFieldKey.NAME];
    const label = elements.create('span', 'field-name', field[ActionFieldKey.LABEL]);
    label.title = field[ActionFieldKey.DOCUMENTATION] || name;
    const row = this.#poseRow(StepsView.#value(step, field), (pose) => this.#set(step, field, pose));
    row.dataset.field = name;
    const capture = elements.button('Use the robot’s live pose', PlanAction.USE_LIVE_POSE, () => this.#editor.useLivePose(identifier, name));
    capture.dataset.field = name;
    return [label, row, capture];
  }

  /**
   * @param {Array<number>|undefined} pose A pose, or undefined for none yet.
   * @param {function(Array<number>): void} onChange Called with the edited pose.
   * @returns {HTMLElement} The controls of the pose's position and heading.
   */
  #poseRow(pose, onChange) {
    const elements = this.#shown;
    const target = pose ?? PlanPose.of(0, 0, 0, 0);
    const position = PlanPose.position(target);
    const row = elements.create('div', 'pose-row');
    const coordinates = ['x', 'y', 'z'].map((axis, index) =>
      elements.number(axis, position[index], PlanAction.POSE, (value) => {
        const moved = [...position];
        moved[index] = value;
        onChange(PlanPose.moved(target, moved));
      }),
    );
    const heading = elements.number('yaw°', PlanPose.degrees(PlanPose.yaw(target)), PlanAction.POSE, (value) => onChange(PlanPose.turned(target, PlanPose.radians(value))));
    row.append(...coordinates, heading);
    return row;
  }

  /**
   * @param {Object} step A step of the edited robot.
   * @param {Map<string, string|null>} observations What its constraints observe live.
   * @returns {HTMLElement} The step's constraints, each removable, with what the live
   *   publisher observes.
   */
  #chips(step, observations) {
    const elements = this.#shown;
    const identifier = step[PlanStepKey.IDENTIFIER];
    const chips = elements.create('div', ViewClass.CHIPS);
    (step[PlanStepKey.CONSTRAINTS] ?? []).forEach((constraint, index) => {
      const chip = elements.create('span', ViewClass.CHIP);
      const monitor = constraint[StepConstraintKey.MONITOR_TYPE] ?? '';
      chip.title = constraint[StepConstraintKey.SWITCH] ?? monitor;
      chip.append(elements.create('span', '', constraint[StepConstraintKey.TEXT]));
      if (monitor) chip.append(elements.create('span', 'badge', monitor));
      const observed = observations.get(constraint[StepConstraintKey.TEXT]);
      if (observed) {
        const badge = elements.create('span', 'observation', observed);
        badge.dataset.observation = observed;
        chip.append(badge);
      }
      chip.append(elements.button('×', PlanAction.DETACH_CONSTRAINT, () => this.#edit(() => this.#editor.state.detachConstraint(identifier, index))));
      chips.append(chip);
    });
    return chips;
  }

  /**
   * @param {string} text A drag's text.
   * @param {number} index Where a dropped action goes.
   * @param {string=} identifier The step a constraint was dropped on.
   */
  #dropped(text, index, identifier) {
    const action = DragText.valueOf(text, DragKind.STEP);
    if (action !== null) {
      this.#add(action, index);
      return;
    }
    const skill = DragText.valueOf(text, DragKind.SKILL);
    if (skill !== null) {
      this.#addSkill(skill, index);
      return;
    }
    const sentence = DragText.valueOf(text, DragKind.CONSTRAINT);
    if (sentence !== null && identifier !== undefined) this.#editor.attachConstraint(identifier, sentence);
  }

  /**
   * @param {string} action An action's class name.
   * @param {number} index Where the step goes.
   */
  #add(action, index) {
    this.#edit(() => this.#editor.state.addStep(action, index));
  }

  /**
   * @param {string} skill A skill of the robot profile.
   * @param {number} index Where the step goes.
   */
  #addSkill(skill, index) {
    this.#edit(() => this.#editor.state.addSkill(skill, index));
  }

  /**
   * @param {function(): void} change A change of the plan.
   */
  #edit(change) {
    this.#editor.edit(change);
  }

  /**
   * Remove every listener.
   */
  dispose() {
    this.#shown.dispose();
    this.#frame.dispose();
  }
}

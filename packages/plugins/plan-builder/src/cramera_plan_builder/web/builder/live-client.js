/**
 * The live publisher a staged or performing demo publishes on, as the builder page
 * calls it, and how the live world's names map to the plan's.
 */

import {
  ConstraintActivation,
  ConstraintOperation,
  ConstraintRequestKey,
  LiveObjectKey,
  LivePlanKey,
  LiveParameter,
  LivePlanNodeKey,
  LiveRoute,
  MonitorSpecificationKey,
  MoveRequestKey,
  ObjectCatalogKey,
  PlanNodeReferenceKey,
  PlanStepKey,
  RobotInstanceKey,
  RobotRosterKey,
  StepConstraintKey,
} from '../../../protocol.js';
import { JsonClient } from './builder-api.js';
import { PlanPose } from './poses.js';

/**
 * What separates a robot's namespace from its name in the live world.
 */
const NAMESPACE_SEPARATOR = '/';

// %% client

export class LiveClient {
  /**
   * Sends and decodes the requests.
   * @type {JsonClient}
   */
  #client;

  /**
   * The publisher's base URL, without a trailing `/`.
   * @type {string}
   */
  #base;

  /**
   * @param {function(string, Object=): Promise<Response>} fetch Fetches a URL.
   * @param {string} base The publisher's base URL.
   */
  constructor(fetch, base) {
    this.#client = new JsonClient(fetch);
    this.#base = base.replace(/\/$/, '');
  }

  /**
   * @param {string} route A `LiveRoute` value.
   * @returns {string} The route's URL.
   */
  url(route) {
    return `${this.#base}${route}`;
  }

  /**
   * @returns {Promise<Object>} What the publisher says about itself.
   */
  info() {
    return this.#client.get(this.url(LiveRoute.INFO));
  }

  /**
   * @returns {Promise<Object>} The free objects with their keys.
   */
  objects() {
    return this.#client.get(this.url(LiveRoute.OBJECTS));
  }

  /**
   * @returns {Promise<Object>} Every free object's pose by key, moves included.
   */
  capturedObjects() {
    return this.#client.get(this.url(LiveRoute.CAPTURED_OBJECTS));
  }

  /**
   * @returns {Promise<Object>} The robots with their poses and postures.
   */
  robots() {
    return this.#client.get(this.url(LiveRoute.ROBOTS));
  }

  /**
   * @returns {Promise<Object>} The executing plan.
   */
  plan() {
    return this.#client.get(this.url(LiveRoute.PLAN));
  }

  /**
   * @param {string} key The key of an image a perception step took.
   * @returns {string} The image's URL.
   */
  perceptionImageUrl(key) {
    return `${this.url(LiveRoute.PERCEPTION_IMAGE)}?${LiveParameter.KEY}=${encodeURIComponent(key)}`;
  }

  /**
   * @returns {Promise<Object>} The annotated surfaces.
   */
  surfaces() {
    return this.#client.get(this.url(LiveRoute.SURFACES));
  }

  /**
   * @param {string} key A free object's key.
   * @param {Array<number>} pose Where it goes.
   * @param {boolean} final Whether the drag ended.
   * @returns {Promise<Object>} The acknowledgement.
   */
  move(key, pose, final) {
    return this.#client.post(this.url(LiveRoute.MOVE), {
      [MoveRequestKey.OBJECT]: key,
      [MoveRequestKey.POSITION]: PlanPose.position(pose),
      [MoveRequestKey.QUATERNION]: PlanPose.orientation(pose),
      [MoveRequestKey.FINAL]: final,
    });
  }

  /**
   * @param {Object} request A `ConstraintRequest` document.
   * @returns {Promise<Object>} The plan with the constraint.
   */
  attach(request) {
    return this.#client.post(this.url(LiveRoute.CONSTRAINT), request);
  }
}

// %% names

export class LiveNames {
  /**
   * @param {Object} catalog The live object catalog.
   * @returns {Map<string, string>} Each object's key by its name, which is the plan's
   *   object identity.
   */
  static objectKeys(catalog) {
    return new Map((catalog[ObjectCatalogKey.OBJECTS] ?? []).map((item) => [item[LiveObjectKey.IDENTIFIER], item[LiveObjectKey.KEY]]));
  }

  /**
   * @param {Object} roster The live robots.
   * @param {Array<string>} identifiers The plan's robot identities, which namespace the
   *   live robots' names.
   * @returns {Map<string, Object>} The live robot of each plan robot found.
   */
  static robots(roster, identifiers) {
    const found = new Map();
    for (const instance of roster[RobotRosterKey.ROBOTS] ?? []) {
      const identifier = identifiers.find((candidate) => instance[RobotInstanceKey.LABEL].startsWith(`${candidate}${NAMESPACE_SEPARATOR}`));
      if (identifier !== undefined) found.set(identifier, instance);
    }
    return found;
  }

  /**
   * @param {Object} plan The live plan.
   * @returns {Array<Object>} The children of the plan's root, in the order they run:
   *   the nodes of the performing robot's steps.
   */
  static stepNodes(plan) {
    const nodes = plan[LivePlanKey.NODES] ?? [];
    const root = nodes.find((node) => node[LivePlanNodeKey.PARENT] === undefined || node[LivePlanNodeKey.PARENT] === null);
    if (!root) return [];
    return nodes.filter((node) => node[LivePlanNodeKey.PARENT] === root[LivePlanNodeKey.IDENTIFIER]);
  }

  /**
   * @param {Object} plan The live plan.
   * @param {Array<Object>} steps The performing robot's steps.
   * @returns {Array<Object>} A `ConstraintRequest` for every constraint with a monitor,
   *   attached to its step's node and observed from the next motion on.
   */
  static constraintRequests(plan, steps) {
    const nodes = LiveNames.stepNodes(plan);
    const requests = [];
    steps.forEach((step, index) => {
      const node = nodes[index];
      if (!node) return;
      for (const constraint of step[PlanStepKey.CONSTRAINTS] ?? []) {
        const monitor = constraint[StepConstraintKey.MONITOR];
        if (!monitor) continue;
        requests.push({
          [ConstraintRequestKey.OPERATION]: ConstraintOperation.ATTACH_MONITOR,
          [ConstraintRequestKey.TEXT]: constraint[StepConstraintKey.TEXT],
          [ConstraintRequestKey.APPLY]: ConstraintActivation.NEXT_ACTIVATION,
          [ConstraintRequestKey.TARGET_PLAN_NODE]: {
            [PlanNodeReferenceKey.IDENTIFIER]: node[LivePlanNodeKey.IDENTIFIER],
            [PlanNodeReferenceKey.KIND]: node[LivePlanNodeKey.KIND],
            [PlanNodeReferenceKey.LABEL]: node[LivePlanNodeKey.LABEL],
          },
          [ConstraintRequestKey.MONITOR]: {
            [MonitorSpecificationKey.TYPE]: monitor[MonitorSpecificationKey.TYPE],
            [MonitorSpecificationKey.PARAMETERS]: monitor[MonitorSpecificationKey.PARAMETERS] ?? {},
          },
        });
      }
    });
    return requests;
  }
}

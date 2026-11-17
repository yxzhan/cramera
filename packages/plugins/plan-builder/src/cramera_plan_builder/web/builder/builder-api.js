/**
 * The plan builder's routes as the builder page calls them.
 */

import { DetectionRegionKey, EnvironmentAnnotationsParameter, ErrorKey, PlanBuilderName, PlanBuilderRoute, PlanRecordKey, ReachRequestKey, RegionKind, RunRequestKey, ScanRequestKey, UrdfEnvironmentKey, UrdfFolderParameter, ValidationRequestKey, ViewerRoute } from '../../../protocol.js';

/**
 * The media type of JSON bodies, which also explain errors.
 */
const JSON_MEDIA_TYPE = 'application/json';

/**
 * The HTTP methods the page uses.
 */
const Method = Object.freeze({ GET: 'GET', POST: 'POST' });

/**
 * The header naming a body's media type.
 */
const CONTENT_TYPE = 'Content-Type';

/**
 * The cache mode of reads: live answers change between requests.
 */
const NO_STORE = 'no-store';

// %% errors

/**
 * A request the server refused, with the reason it gave.
 */
export class RequestRefused extends Error {
  /**
   * @param {number} status The answer's HTTP status.
   * @param {string} reason The reason the server gave, or the status text.
   * @param {string|null} output The whole output of the process behind the refusal,
   *   or null when the server gave none.
   */
  constructor(status, reason, output = null) {
    super(reason);
    /**
     * The answer's HTTP status.
     * @type {number}
     */
    this.status = status;
    /**
     * The whole output of the process behind the refusal, or null.
     * @type {string|null}
     */
    this.output = output;
  }
}

// %% JSON over HTTP

export class JsonClient {
  /**
   * Fetches a URL; `window.fetch` in the browser.
   * @type {function(string, Object=): Promise<Response>}
   */
  #fetch;

  /**
   * @param {function(string, Object=): Promise<Response>} fetch Fetches a URL.
   */
  constructor(fetch) {
    this.#fetch = fetch;
  }

  /**
   * @param {string} url A URL answering JSON.
   * @returns {Promise<Object>} The decoded answer.
   */
  get(url) {
    return this.#send(url, { method: Method.GET, cache: NO_STORE });
  }

  /**
   * @param {string} url A URL accepting JSON.
   * @param {Object} body The body.
   * @returns {Promise<Object>} The decoded answer.
   */
  post(url, body) {
    return this.#send(url, { method: Method.POST, headers: { [CONTENT_TYPE]: JSON_MEDIA_TYPE }, body: JSON.stringify(body) });
  }

  /**
   * @param {string} url The URL.
   * @param {Object} options The request's options.
   * @returns {Promise<Object>} The decoded answer.
   * @throws {RequestRefused} When the answer has an error status.
   */
  async #send(url, options) {
    const answer = await this.#fetch(url, options);
    const explained = (answer.headers.get(CONTENT_TYPE) ?? '').startsWith(JSON_MEDIA_TYPE);
    if (answer.ok) return answer.json();
    if (!explained) throw new RequestRefused(answer.status, answer.statusText);
    const refusal = await answer.json();
    throw new RequestRefused(answer.status, refusal[ErrorKey.ERROR], refusal[ErrorKey.OUTPUT] ?? null);
  }
}

// %% plan builder

export class PlanBuilderApi {
  /**
   * Sends and decodes the requests.
   * @type {JsonClient}
   */
  #client;

  /**
   * The viewer's root URL, ending in `/`.
   * @type {string}
   */
  #viewer;

  /**
   * @param {function(string, Object=): Promise<Response>} fetch Fetches a URL.
   * @param {string} viewer The viewer's root URL, ending in `/`.
   */
  constructor(fetch, viewer) {
    this.#client = new JsonClient(fetch);
    this.#viewer = viewer;
  }

  /**
   * @param {string} route A `PlanBuilderRoute` value, possibly followed by a path.
   * @returns {string} The route's URL.
   */
  url(route) {
    return new URL(`${ViewerRoute.PLUGIN_API.slice(1)}${PlanBuilderName.PLUGIN}/${route}`, this.#viewer).href;
  }

  /**
   * @returns {Promise<Object>} The catalog.
   */
  catalog() {
    return this.#client.get(this.url(PlanBuilderRoute.CATALOG));
  }

  /**
   * @param {string} path A URDF or xacro file on the viewer's machine.
   * @returns {Promise<Object>} The `LoadedEnvironment`: the environment bundle written
   *   from it and the meshes that were not found.
   */
  loadEnvironment(path) {
    return this.#client.post(this.url(PlanBuilderRoute.ENVIRONMENTS), { [UrdfEnvironmentKey.PATH]: path });
  }

  /**
   * @param {string} environment An environment bundle's name.
   * @returns {Promise<Object>} Its `EnvironmentAnnotations`: each annotation's class
   *   and name, as a plan run finds them.
   */
  environmentAnnotations(environment) {
    const url = new URL(this.url(PlanBuilderRoute.ENVIRONMENT_ANNOTATIONS));
    url.searchParams.set(EnvironmentAnnotationsParameter.ENVIRONMENT, environment);
    return this.#client.get(url.href);
  }

  /**
   * @param {string|null} path A folder on the viewer's machine, or null for the home
   *   folder.
   * @returns {Promise<Object>} The `UrdfFolder`: its folders and URDF files.
   */
  urdfFolder(path) {
    const url = new URL(this.url(PlanBuilderRoute.URDF_FOLDER));
    if (path !== null) url.searchParams.set(UrdfFolderParameter.PATH, path);
    return this.#client.get(url.href);
  }

  /**
   * @returns {Promise<Object>} The saved plans.
   */
  plans() {
    return this.#client.get(this.url(PlanBuilderRoute.PLANS));
  }

  /**
   * @param {string} identifier A saved plan's identity.
   * @returns {Promise<Object>} The plan record.
   */
  plan(identifier) {
    return this.#client.get(this.url(`${PlanBuilderRoute.PLAN}${encodeURIComponent(identifier)}`));
  }

  /**
   * @param {Object} plan A plan document.
   * @param {string|null} identifier The saved plan it replaces, or null for a new one.
   * @returns {Promise<Object>} The saved plan record.
   */
  save(plan, identifier) {
    const record = { [PlanRecordKey.DOCUMENT]: plan };
    if (identifier !== null) record[PlanRecordKey.IDENTIFIER] = identifier;
    return this.#client.post(this.url(PlanBuilderRoute.PLANS), record);
  }

  /**
   * @param {Object} request A `ConstraintText` document.
   * @returns {Promise<Object>} The compiled `StepConstraint`.
   */
  compile(request) {
    return this.#client.post(this.url(PlanBuilderRoute.CONSTRAINT), request);
  }

  /**
   * @param {Object} plan A plan document.
   * @returns {Promise<Object>} The generated demo, not written.
   */
  demo(plan) {
    return this.#client.post(this.url(PlanBuilderRoute.DEMO), plan);
  }

  /**
   * @param {Object} plan A plan document.
   * @returns {Promise<Object>} Where the demo was written and how to run it.
   */
  writeDemo(plan) {
    return this.#client.post(this.url(PlanBuilderRoute.DEMOS), plan);
  }

  /**
   * @param {Object} plan A plan document.
   * @param {string} mode A `RunMode` value.
   * @param {boolean} overrideValidation Whether a run needing a validated plan starts
   *   without one, as developer mode allows.
   * @returns {Promise<Object>} The started demo's status.
   */
  run(plan, mode, overrideValidation = false) {
    const override = overrideValidation ? { [RunRequestKey.OVERRIDE_VALIDATION]: true } : {};
    return this.#client.post(this.url(PlanBuilderRoute.RUN), { [RunRequestKey.PLAN]: plan, [RunRequestKey.MODE]: mode, ...override });
  }

  /**
   * @param {Object} plan A plan document.
   * @returns {Promise<Object>} The `ValidationReport`: how each step of the selected
   *   robot fared in a copy of the twin.
   */
  validate(plan) {
    return this.#client.post(this.url(PlanBuilderRoute.VALIDATE), { [ValidationRequestKey.PLAN]: plan });
  }

  /**
   * @param {string} model A robot model's name.
   * @returns {Promise<Object>} The `RobotReach`: what each arm of the model reaches and
   *   the table it works at.
   */
  reach(model) {
    return this.#client.post(this.url(PlanBuilderRoute.REACH), { [ReachRequestKey.ROBOT]: model });
  }

  /**
   * @returns {Promise<Object>} The status of the demo started last.
   */
  runStatus() {
    return this.#client.get(this.url(PlanBuilderRoute.RUN));
  }

  /**
   * @returns {Promise<Object>} The `RobotPosture` the real robot's drivers reported
   *   last; empty while the plan builder is not attached to the real robot.
   */
  posture() {
    return this.#client.get(this.url(PlanBuilderRoute.POSTURE));
  }

  /**
   * @returns {Promise<Object>} The stopped demo's status.
   */
  stop() {
    return this.#client.post(this.url(PlanBuilderRoute.RUN_STOP), {});
  }

  /**
   * @returns {Promise<Object>} The status of the demo started last, its safety stop
   *   acknowledged.
   */
  acknowledge() {
    return this.#client.post(this.url(PlanBuilderRoute.RUN_ACKNOWLEDGE), {});
  }

  /**
   * @param {Object} plan A plan document, whose robot looks.
   * @param {string} region A `RegionKind` value: where the robot looks.
   * @returns {Promise<Object>} The `SceneScan`: the plan's objects after the scan.
   */
  scan(plan, region = RegionKind.TABLE) {
    return this.#client.post(this.url(PlanBuilderRoute.SCAN), { [ScanRequestKey.PLAN]: plan, [ScanRequestKey.REGION]: { [DetectionRegionKey.KIND]: region } });
  }

  /**
   * @param {string} identifier An object model's identifier.
   * @returns {string} The URL of its mesh or, for a library model, of its URDF; the
   *   path keeps its slashes, so the files a URDF names resolve beside it.
   */
  objectUrl(identifier) {
    return this.url(`${PlanBuilderRoute.OBJECT_FILES}${identifier.split('/').map(encodeURIComponent).join('/')}`);
  }

  /**
   * @param {string} name A robot model's name.
   * @returns {string} The URL of its URDF, beside which the meshes it names resolve.
   */
  robotUrl(name) {
    const robot = encodeURIComponent(name);
    return this.url(`${PlanBuilderRoute.ROBOT_FILES}${robot}/${robot}.urdf`);
  }
}

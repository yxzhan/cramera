/**
 * Placing by hand: a marker dragged across the scene, which slides over the surface
 * below it, turns about the vertical only and shows whether an arm may reach it, and
 * the robot profile's named zones laid out on the robot's table.
 *
 * What a robot reaches comes from the plan builder as a `RobotReach`, written in the
 * frame of the pose the plan stands the robot at; a point outside every arm's sphere is
 * out of reach for certain, while the plan's validation stays the full check.
 */

import { ArmReachKey, RectangleKey, RobotReachKey, TableTopKey, ZoneKey } from '../../../protocol.js';
import { PlanPose } from './poses.js';

/**
 * How high above the surface a zone is drawn, in metres, so it does not flicker with the
 * table's top.
 */
const ZONE_LIFT = 0.002;

// %% the robot's frame

export class RobotFrame {
  /**
   * @param {Array<number>} pose The pose the plan stands the robot at.
   */
  constructor(pose) {
    /**
     * The robot's position.
     * @type {Array<number>}
     */
    this.position = PlanPose.position(pose);
    /**
     * The robot's heading about the vertical, in radians.
     * @type {number}
     */
    this.yaw = PlanPose.yaw(pose);
  }

  /**
   * @param {Array<number>} point A point `[x, y, z]` in world coordinates.
   * @returns {Array<number>} The point in the robot's frame.
   */
  toRobot([x, y, z]) {
    const [dx, dy, dz] = [x - this.position[0], y - this.position[1], z - this.position[2]];
    const [cos, sin] = [Math.cos(this.yaw), Math.sin(this.yaw)];
    return [cos * dx + sin * dy, -sin * dx + cos * dy, dz];
  }

  /**
   * @param {Array<number>} point A point `[x, y, z]` in the robot's frame.
   * @returns {Array<number>} The point in world coordinates.
   */
  toWorld([x, y, z]) {
    const [cos, sin] = [Math.cos(this.yaw), Math.sin(this.yaw)];
    return [this.position[0] + cos * x - sin * y, this.position[1] + sin * x + cos * y, this.position[2] + z];
  }
}

// %% free placing

export class FreePlace {
  /**
   * What the robot reaches, or null while it is not known.
   * @type {Object|null}
   */
  #reach;

  /**
   * The robot's frame.
   * @type {RobotFrame}
   */
  #frame;

  /**
   * @param {Object|null} reach The robot model's `RobotReach`, or null while it is not
   *   known.
   * @param {Array<number>} robotPose The pose the plan stands the robot at.
   */
  constructor(reach, robotPose) {
    this.#reach = reach;
    this.#frame = new RobotFrame(robotPose);
  }

  /**
   * @returns {boolean} Whether what the robot reaches is known.
   */
  get known() {
    return this.#reach !== null;
  }

  /**
   * @param {Array<number>} pose Where a marker was dragged or typed.
   * @returns {Array<number>} The marker upright at the same position and heading,
   *   resting on the surface below it: the table's top above the table, else the floor;
   *   its height unchanged while the robot's reach is not known.
   */
  snapped(pose) {
    const [x, y, z] = PlanPose.position(pose);
    const height = this.#reach === null ? z : this.#surfaceHeight(x, y);
    return PlanPose.of(PlanPose.rounded(x), PlanPose.rounded(y), PlanPose.rounded(height), PlanPose.yaw(pose));
  }

  /**
   * @param {Array<number>} pose A marker's pose.
   * @returns {boolean|null} Whether an arm of the robot may reach it; false only where
   *   none can; null while the robot's reach is not known.
   */
  reaches(pose) {
    if (this.#reach === null) return null;
    const point = this.#frame.toRobot(PlanPose.position(pose));
    return (this.#reach[RobotReachKey.ARMS] ?? []).some((arm) => {
      const shoulder = arm[ArmReachKey.SHOULDER];
      return Math.hypot(point[0] - shoulder[0], point[1] - shoulder[1], point[2] - shoulder[2]) <= arm[ArmReachKey.RADIUS];
    });
  }

  /**
   * @param {Array<Object>} zones The robot profile's `Zone`s.
   * @returns {Array<{identifier: string, label: string, corners: Array<Array<number>>}>}
   *   Each zone's corners in world coordinates, in order around it, just above the
   *   table's top; none while the robot's reach and table are not known.
   */
  zones(zones) {
    const table = this.#reach?.[RobotReachKey.TABLE];
    if (!table) return [];
    const height = table[TableTopKey.HEIGHT] + ZONE_LIFT;
    return zones.map((zone) => {
      const area = zone[ZoneKey.AREA];
      const [minX, maxX, minY, maxY] = [area[RectangleKey.MIN_X], area[RectangleKey.MAX_X], area[RectangleKey.MIN_Y], area[RectangleKey.MAX_Y]];
      const corners = [
        [minX, minY],
        [maxX, minY],
        [maxX, maxY],
        [minX, maxY],
      ].map(([x, y]) => this.#frame.toWorld([x, y, height]));
      return { identifier: zone[ZoneKey.IDENTIFIER], label: zone[ZoneKey.LABEL], corners };
    });
  }

  /**
   * @param {number} x An x coordinate in world coordinates.
   * @param {number} y A y coordinate in world coordinates.
   * @returns {number} The world height of the surface below the point: the table's
   *   top, which stands with the robot, else the floor.
   */
  #surfaceHeight(x, y) {
    const [robotX, robotY] = this.#frame.toRobot([x, y, 0]);
    const table = this.#reach[RobotReachKey.TABLE];
    const area = table?.[TableTopKey.AREA];
    const above = area && robotX >= area[RectangleKey.MIN_X] && robotX <= area[RectangleKey.MAX_X] && robotY >= area[RectangleKey.MIN_Y] && robotY <= area[RectangleKey.MAX_Y];
    if (!above) return this.#reach[RobotReachKey.FLOOR_HEIGHT] ?? 0;
    return this.#frame.toWorld([0, 0, table[TableTopKey.HEIGHT]])[2];
  }
}

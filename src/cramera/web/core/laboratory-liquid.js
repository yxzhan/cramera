/* Render authoritative liquid volumes in the laboratory's metric Z-up frame. */
(function (global, factory) {
  'use strict';
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.LaboratoryLiquid = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';

  // %% closed vessel surfaces
  const RADIAL_SEGMENTS = 64;
  const ROUNDING_SEGMENTS = 12;
  const PLANE_EPSILON = 1e-12;
  const EMPTY_VOLUME = 1e-8;
  const VERTEX_CAPACITY = 12000;
  const PUDDLE_DEPTH = .00055;
  const CUBIC_METRES_PER_MILLILITRE = 1e-6;
  const STREAM_RADIAL_SEGMENTS = 16;
  const STREAM_MINIMUM_PACKETS = 3;
  const STREAM_MAXIMUM_GAP = .015;
  const STREAM_MAXIMUM_EMISSION_INTERVAL = .025;
  const STREAM_MAXIMUM_TURN_COSINE = .5;
  const WATER_REFLECTIVITY = .357;
  const AUTHORED_CONTENTS = new Set(['Amber solution', 'Teal solution']);
  const dot = (first, second) => first.reduce((sum, value, axis) => sum + value * second[axis], 0);
  const subtract = (first, second) => first.map((value, axis) => value - second[axis]);
  const cross = (first, second) => [
    first[1] * second[2] - first[2] * second[1],
    first[2] * second[0] - first[0] * second[2],
    first[0] * second[1] - first[1] * second[0],
  ];
  const normalize = vector => {
    const length = Math.hypot(...vector);
    return vector.map(value => value / length);
  };
  const pointKey = point => point.map(value => value.toFixed(12)).join(',');

  class SurfaceVertex {
    /** Keep a boundary position and its smooth outward shading normal together. */
    constructor(position, normal) { this.position = position; this.normal = normal; }

    /** Interpolate a boundary crossing while preserving its shading normal. */
    between(other, fraction) {
      return new SurfaceVertex(
        this.position.map((value, axis) => value + fraction * (other.position[axis] - value)),
        normalize(this.normal.map((value, axis) => value + fraction * (other.normal[axis] - value))),
      );
    }
  }

  class VesselSurface {
    /** Tessellate a closed interior with an elliptical rounded bottom. */
    constructor(parameters) {
      this.faces = [];
      const radius = parameters.radius;
      const rounding = parameters.roundingRadius;
      const centre = parameters.bottom + rounding;
      const rings = [[new SurfaceVertex([0, 0, parameters.bottom], [0, 0, -1])]];
      for (let elevation = 1; elevation <= ROUNDING_SEGMENTS + 1; elevation += 1) {
        const angle = Math.PI * elevation / (2 * ROUNDING_SEGMENTS);
        const ringRadius = elevation > ROUNDING_SEGMENTS ? radius : radius * Math.sin(angle);
        const height = elevation > ROUNDING_SEGMENTS ? parameters.rim : centre - rounding * Math.cos(angle);
        const ring = [];
        for (let segment = 0; segment < RADIAL_SEGMENTS; segment += 1) {
          const azimuth = 2 * Math.PI * segment / RADIAL_SEGMENTS;
          const normal = elevation >= ROUNDING_SEGMENTS
            ? [Math.cos(azimuth), Math.sin(azimuth), 0]
            : normalize([Math.sin(angle) * Math.cos(azimuth) / radius,
              Math.sin(angle) * Math.sin(azimuth) / radius, -Math.cos(angle) / rounding]);
          ring.push(new SurfaceVertex([ringRadius * Math.cos(azimuth), ringRadius * Math.sin(azimuth), height], normal));
        }
        rings.push(ring);
      }
      for (let ring = 1; ring < rings.length; ring += 1) {
        const previous = rings[ring - 1], current = rings[ring];
        for (let segment = 0; segment < RADIAL_SEGMENTS; segment += 1) {
          const next = (segment + 1) % RADIAL_SEGMENTS;
          this.faces.push(previous.length === 1
            ? [previous[0], current[next], current[segment]]
            : [previous[segment], previous[next], current[next], current[segment]]);
        }
      }
      this.faces.push(rings[rings.length - 1].map(vertex => new SurfaceVertex(vertex.position, [0, 0, 1])));
    }

    /** Clip every face below the supplied local plane and close its free surface. */
    clip(parameters) {
      const output = {positions: [], normals: []};
      if (!(parameters.volumeMl > EMPTY_VOLUME)) return output;
      const boundary = new Map();
      const normal = parameters.normal;
      for (const face of this.faces) {
        const polygon = [];
        for (let index = 0; index < face.length; index += 1) {
          const first = face[index], second = face[(index + 1) % face.length];
          const firstDistance = dot(normal, first.position) - parameters.offset;
          const secondDistance = dot(normal, second.position) - parameters.offset;
          const firstInside = firstDistance <= PLANE_EPSILON;
          const secondInside = secondDistance <= PLANE_EPSILON;
          if (firstInside) polygon.push(first);
          if (firstInside !== secondInside) {
            const vertex = first.between(second, firstDistance / (firstDistance - secondDistance));
            polygon.push(vertex);
            boundary.set(pointKey(vertex.position), vertex.position);
          }
        }
        this.appendPolygon(polygon, output);
      }
      if (boundary.size >= 3) {
        const points = [...boundary.values()];
        const centre = [0, 1, 2].map(axis => points.reduce((sum, point) => sum + point[axis], 0) / points.length);
        const surfaceNormal = normalize(normal);
        const reference = Math.abs(surfaceNormal[2]) > .9 ? [1, 0, 0] : [0, 0, 1];
        const horizontal = normalize(cross(reference, surfaceNormal));
        const vertical = cross(surfaceNormal, horizontal);
        points.sort((first, second) => {
          const firstOffset = subtract(first, centre), secondOffset = subtract(second, centre);
          return Math.atan2(dot(firstOffset, vertical), dot(firstOffset, horizontal))
            - Math.atan2(dot(secondOffset, vertical), dot(secondOffset, horizontal));
        });
        const middle = new SurfaceVertex(centre, surfaceNormal);
        for (let index = 0; index < points.length; index += 1) {
          this.appendPolygon([middle, new SurfaceVertex(points[index], surfaceNormal),
            new SurfaceVertex(points[(index + 1) % points.length], surfaceNormal)], output);
        }
      }
      return output;
    }

    /** Triangulate a convex polygon without zero-length clipping edges. */
    appendPolygon(vertices, output) {
      const polygon = vertices.filter((vertex, index) => !index
        || pointKey(vertex.position) !== pointKey(vertices[index - 1].position));
      if (polygon.length > 1 && pointKey(polygon[0].position) === pointKey(polygon[polygon.length - 1].position)) polygon.pop();
      for (let index = 1; index + 1 < polygon.length; index += 1) {
        const triangle = [polygon[0], polygon[index], polygon[index + 1]];
        const area = cross(subtract(triangle[1].position, triangle[0].position), subtract(triangle[2].position, triangle[0].position));
        if (Math.hypot(...area) <= PLANE_EPSILON ** 2) continue;
        for (const vertex of triangle) {
          output.positions.push(...vertex.position);
          output.normals.push(...vertex.normal);
        }
      }
    }
  }

  /** Return a closed, clipped fluid mesh without depending on a renderer. */
  function buildSurface(parameters) { return new VesselSurface(parameters).clip(parameters); }

  // %% contained liquid and pooled spill geometry
  function makeMaterial(three) {
    return new three.MeshPhysicalMaterial({
      color: 0xffffff, metalness: 0, roughness: .035, transmission: .76,
      transparent: true, opacity: .97, depthWrite: false,
      reflectivity: WATER_REFLECTIVITY, clearcoat: .12,
      clearcoatRoughness: .035, envMapIntensity: 1.15,
    });
  }

  class ContainedLiquid {
    /** Replace an object's authored fill while retaining its glass and labels. */
    constructor(three, object, parameters) {
      this.three = three;
      this.object = object;
      this.surface = new VesselSurface(parameters);
      this.authored = [];
      object.traverse(child => {
        if (!child.isMesh) return;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        if (!materials.some(material => material && AUTHORED_CONTENTS.has(material.name))) return;
        this.authored.push({object: child, visible: child.visible});
        child.visible = false;
      });
      const geometry = new three.BufferGeometry();
      geometry.setAttribute('position', new three.BufferAttribute(new Float32Array(VERTEX_CAPACITY * 3), 3).setUsage(three.DynamicDrawUsage));
      geometry.setAttribute('normal', new three.BufferAttribute(new Float32Array(VERTEX_CAPACITY * 3), 3).setUsage(three.DynamicDrawUsage));
      this.mesh = new three.Mesh(geometry, makeMaterial(three));
      this.mesh.name = 'Simulated liquid';
      this.mesh.castShadow = false;
      this.mesh.receiveShadow = true;
      this.mesh.userData.preserveMaterials = true;
      this.mesh.frustumCulled = false;
      object.add(this.mesh);
    }

    /** Upload the authoritative local clipping surface into reusable buffers. */
    update(parameters) {
      const surface = this.surface.clip(parameters);
      const geometry = this.mesh.geometry;
      for (const [attribute, values] of [['position', surface.positions], ['normal', surface.normals]]) {
        const buffer = geometry.getAttribute(attribute);
        if (values.length > buffer.array.length) throw new RangeError('Liquid surface exceeds its vertex capacity.');
        buffer.array.set(values);
        buffer.updateRange.offset = 0;
        buffer.updateRange.count = values.length;
        buffer.needsUpdate = true;
      }
      geometry.setDrawRange(0, surface.positions.length / 3);
      this.mesh.visible = surface.positions.length > 0;
      this.mesh.material.color.setRGB(...parameters.color);
    }

    /** Restore imported content and release only resources owned by the overlay. */
    destroy() {
      this.authored.forEach(entry => { entry.object.visible = entry.visible; });
      this.object.remove(this.mesh);
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
    }
  }

  class SpillPool {
    /** Reuse sphere meshes for airborne droplets or flattened worktop puddles. */
    constructor(three, root, puddles) {
      this.three = three;
      this.root = root;
      this.puddles = puddles;
      this.geometry = new three.SphereGeometry(1, puddles ? 32 : 20, 12);
      this.meshes = [];
    }

    /** Apply positions, colours and physical volumes without adding simulation. */
    update(entries) {
      while (this.meshes.length < entries.length) {
        const mesh = new this.three.Mesh(this.geometry, makeMaterial(this.three));
        mesh.name = this.puddles ? 'Spilled liquid' : 'Liquid droplet';
        mesh.castShadow = false;
        this.meshes.push(mesh);
        this.root.add(mesh);
      }
      this.meshes.forEach((mesh, index) => {
        const entry = entries[index];
        mesh.visible = Boolean(entry);
        if (!entry) return;
        mesh.position.fromArray(entry.position);
        mesh.material.color.setRGB(...entry.color);
        if (this.puddles) {
          const radius = Math.sqrt(Math.max(0, entry.volumeMl) * CUBIC_METRES_PER_MILLILITRE / (4 * Math.PI * PUDDLE_DEPTH / 3));
          mesh.scale.set(radius, radius, PUDDLE_DEPTH);
        } else mesh.scale.setScalar(entry.radius);
      });
    }

    /** Release the shared geometry and each pooled optical material. */
    destroy() {
      this.meshes.forEach(mesh => { this.root.remove(mesh); mesh.material.dispose(); });
      this.geometry.dispose();
      this.meshes = [];
    }
  }

  // %% continuous airborne surfaces
  class StreamSurface {
    /** Reconstruct a closed stream with the conserved volume of its flight packets. */
    constructor(packets, lip) {
      this.origin = packets[0].position;
      this.volume = packets.reduce((sum, packet) => sum + 4 * Math.PI * packet.radius ** 3 / 3, 0);
      this.color = packets[0].color.map((value, axis) => value + packets.reduce((sum, packet) =>
        sum + (packet.color[axis] - value) * 4 * Math.PI * packet.radius ** 3 / 3, 0) / this.volume);
      this.points = packets.map(packet => subtract(packet.position, this.origin));
      const widths = packets.map((packet, index) => {
        const previous = this.points[Math.max(0, index - 1)], next = this.points[Math.min(packets.length - 1, index + 1)];
        const length = Math.hypot(...subtract(this.points[index], previous)) + Math.hypot(...subtract(next, this.points[index]));
        const span = length / (index > 0 && index + 1 < packets.length ? 2 : 1);
        return Math.sqrt(4 * packet.radius ** 3 / (3 * span));
      });
      this.radii = widths.map((width, index) =>
        (widths[Math.max(0, index - 1)] + 2 * width + widths[Math.min(widths.length - 1, index + 1)]) / 4);
      if (lip && Math.hypot(...subtract(lip, packets[packets.length - 1].position)) > PLANE_EPSILON) {
        this.points.push(subtract(lip, this.origin));
        this.radii.push(this.radii[this.radii.length - 1]);
      }
      this.frames = this.makeFrames();
      const provisional = this.triangulate(1);
      let enclosed = 0;
      for (let index = 0; index < provisional.positions.length; index += 9) {
        enclosed += dot(provisional.positions.slice(index, index + 3),
          cross(provisional.positions.slice(index + 3, index + 6), provisional.positions.slice(index + 6, index + 9))) / 6;
      }
      this.surface = this.triangulate(Math.sqrt(this.volume / Math.abs(enclosed)));
    }

    /** Transport cross-section axes smoothly along the measured packet trajectory. */
    makeFrames() {
      const frames = [];
      this.points.forEach((point, index) => {
        const before = this.points[Math.max(0, index - 1)], after = this.points[Math.min(this.points.length - 1, index + 1)];
        const tangent = normalize(subtract(after, before));
        const previous = frames.length ? frames[frames.length - 1].horizontal : null;
        const reference = Math.abs(tangent[2]) > .9 ? [1, 0, 0] : [0, 0, 1];
        const horizontal = previous
          ? normalize(previous.map((value, axis) => value - tangent[axis] * dot(previous, tangent)))
          : normalize(cross(reference, tangent));
        frames.push({tangent, horizontal, vertical: cross(tangent, horizontal)});
      });
      return frames;
    }

    /** Close smooth cross-sections with end caps and outward-facing triangles. */
    triangulate(scale) {
      const output = {positions: [], normals: []};
      const rings = this.points.map((point, index) => {
        const frame = this.frames[index], radius = this.radii[index] * scale;
        const first = Math.max(0, index - 1), last = Math.min(this.points.length - 1, index + 1);
        const slope = (this.radii[last] - this.radii[first]) * scale / Math.hypot(...subtract(this.points[last], this.points[first]));
        return Array.from({length: STREAM_RADIAL_SEGMENTS}, (_, segment) => {
          const angle = 2 * Math.PI * segment / STREAM_RADIAL_SEGMENTS;
          const radial = frame.horizontal.map((value, axis) => value * Math.cos(angle) + frame.vertical[axis] * Math.sin(angle));
          return new SurfaceVertex(point.map((value, axis) => value + radius * radial[axis]),
            normalize(radial.map((value, axis) => value - slope * frame.tangent[axis])));
        });
      });
      const append = triangle => triangle.forEach(vertex => {
        output.positions.push(...vertex.position);
        output.normals.push(...vertex.normal);
      });
      for (let ring = 0; ring + 1 < rings.length; ring += 1) {
        for (let segment = 0; segment < STREAM_RADIAL_SEGMENTS; segment += 1) {
          const next = (segment + 1) % STREAM_RADIAL_SEGMENTS;
          append([rings[ring][segment], rings[ring][next], rings[ring + 1][next]]);
          append([rings[ring][segment], rings[ring + 1][next], rings[ring + 1][segment]]);
        }
      }
      for (const index of [0, rings.length - 1]) {
        const normal = this.frames[index].tangent.map(value => value * (index ? 1 : -1));
        const centre = new SurfaceVertex(this.points[index], normal);
        for (let segment = 0; segment < STREAM_RADIAL_SEGMENTS; segment += 1) {
          const next = (segment + 1) % STREAM_RADIAL_SEGMENTS;
          const first = new SurfaceVertex(rings[index][segment].position, normal);
          const second = new SurfaceVertex(rings[index][next].position, normal);
          append(index ? [centre, first, second] : [centre, second, first]);
        }
      }
      return output;
    }
  }

  class AirborneLiquid {
    /** Pool continuous stream surfaces separately from detached physical drops. */
    constructor(three, root, getObject) {
      this.three = three;
      this.root = root;
      this.getObject = getObject;
      this.drops = new SpillPool(three, root, false);
      this.meshes = [];
    }

    /** Split source-ordered packets at interrupted discharge or divergent trajectories. */
    chains(entries) {
      const sources = new Map(), chains = [];
      entries.forEach(packet => {
        if (!packet.source) { chains.push([packet]); return; }
        if (!sources.has(packet.source)) sources.set(packet.source, []);
        sources.get(packet.source).push(packet);
      });
      sources.forEach(packets => {
        let chain = [];
        packets.forEach(packet => {
          if (chain.length && !this.connected(chain, packet)) { chains.push(chain); chain = []; }
          chain.push(packet);
        });
        if (chain.length) chains.push(chain);
      });
      return chains;
    }

    /** Require nearby consistent flight samples, with no missing emission interval. */
    connected(chain, packet) {
      const previous = chain[chain.length - 1];
      const displacement = subtract(packet.position, previous.position), distance = Math.hypot(...displacement);
      if (!(distance > PLANE_EPSILON)) return false;
      let maximumGap = STREAM_MAXIMUM_GAP;
      if (Number.isFinite(packet.emittedAt) && Number.isFinite(previous.emittedAt)) {
        const interval = packet.emittedAt - previous.emittedAt;
        if (!(interval > 0 && interval <= STREAM_MAXIMUM_EMISSION_INTERVAL)) return false;
        if (chain.length > 1) {
          const preceding = previous.emittedAt - chain[chain.length - 2].emittedAt;
          if (Number.isFinite(preceding) && interval > preceding * 2.5) return false;
        }
        if (packet.velocity && previous.velocity) {
          const travelled = interval * (Math.hypot(...packet.velocity) + Math.hypot(...previous.velocity)) / 2;
          maximumGap = Math.max(maximumGap, travelled * 1.8 + packet.radius + previous.radius);
        }
      }
      if (distance > maximumGap) return false;
      if (chain.length < 2) return true;
      const entering = normalize(subtract(previous.position, chain[chain.length - 2].position));
      return dot(entering, normalize(displacement)) >= STREAM_MAXIMUM_TURN_COSINE;
    }

    /** Attach only freshly emitted packets to the measured, overflowing source rim. */
    lip(chain, snapshot) {
      const newest = chain[chain.length - 1], previous = chain[chain.length - 2];
      const interval = newest.emittedAt - previous.emittedAt;
      if (!Number.isFinite(snapshot.time) || !(interval > 0)
        || snapshot.time - newest.emittedAt > interval * 1.5) return null;
      const parameters = (snapshot.tubes || {})[newest.source], object = this.getObject(newest.source);
      if (!parameters || !object || !(parameters.volumeMl > EMPTY_VOLUME)) return null;
      const horizontal = Math.hypot(parameters.normal[0], parameters.normal[1]);
      const local = [0, 0, parameters.rim];
      if (horizontal > PLANE_EPSILON) {
        local[0] = -parameters.radius * parameters.normal[0] / horizontal;
        local[1] = -parameters.radius * parameters.normal[1] / horizontal;
      }
      if (parameters.offset < dot(local, parameters.normal) - PLANE_EPSILON) return null;
      const point = new this.three.Vector3().fromArray(local);
      object.updateWorldMatrix(true, false);
      object.localToWorld(point);
      this.root.worldToLocal(point);
      const position = point.toArray();
      if (Math.hypot(...subtract(position, newest.position)) > STREAM_MAXIMUM_GAP) return null;
      return position;
    }

    /** Replace adjacent packet spheres by a shared surface without changing liquid state. */
    update(snapshot) {
      const detached = [], streams = [];
      this.chains(snapshot.droplets || []).forEach(chain => {
        if (chain.length < STREAM_MINIMUM_PACKETS) detached.push(...chain);
        else streams.push(new StreamSurface(chain, this.lip(chain, snapshot)));
      });
      this.drops.update(detached);
      while (this.meshes.length < streams.length) {
        const mesh = new this.three.Mesh(new this.three.BufferGeometry(), makeMaterial(this.three));
        mesh.name = 'Liquid stream';
        mesh.castShadow = false;
        mesh.receiveShadow = true;
        mesh.frustumCulled = false;
        mesh.userData.preserveMaterials = true;
        this.meshes.push(mesh);
        this.root.add(mesh);
      }
      this.meshes.forEach((mesh, index) => {
        const stream = streams[index];
        mesh.visible = Boolean(stream);
        if (!stream) return;
        mesh.position.fromArray(stream.origin);
        mesh.material.color.setRGB(...stream.color);
        for (const [attribute, values] of [['position', stream.surface.positions], ['normal', stream.surface.normals]]) {
          let buffer = mesh.geometry.getAttribute(attribute);
          if (!buffer || buffer.array.length < values.length) {
            buffer = new this.three.BufferAttribute(new Float32Array(Math.max(VERTEX_CAPACITY * 3, values.length)), 3).setUsage(this.three.DynamicDrawUsage);
            mesh.geometry.setAttribute(attribute, buffer);
          }
          buffer.array.set(values);
          buffer.updateRange.offset = 0;
          buffer.updateRange.count = values.length;
          buffer.needsUpdate = true;
        }
        mesh.geometry.setDrawRange(0, stream.surface.positions.length / 3);
      });
    }

    /** Release every airborne surface and its private optical material. */
    destroy() {
      this.drops.destroy();
      this.meshes.forEach(mesh => { this.root.remove(mesh); mesh.geometry.dispose(); mesh.material.dispose(); });
      this.meshes = [];
    }
  }

  class LiquidView {
    /** Bind metric fluid snapshots to independently loaded laboratory objects. */
    constructor(three, worldRoot, getObject) {
      this.three = three;
      this.getObject = getObject;
      this.contents = new Map();
      this.droplets = new AirborneLiquid(three, worldRoot, getObject);
      this.puddles = new SpillPool(three, worldRoot, true);
    }

    /** Render the latest fluid state, including assets which finished loading late. */
    update(snapshot) {
      if (!snapshot) return;
      const tubes = snapshot.tubes || {};
      for (const [key, contents] of this.contents) {
        if (tubes[key] && this.getObject(key) === contents.object) continue;
        contents.destroy();
        this.contents.delete(key);
      }
      for (const [key, parameters] of Object.entries(tubes)) {
        const object = this.getObject(key);
        if (!object) continue;
        if (!this.contents.has(key)) this.contents.set(key, new ContainedLiquid(this.three, object, parameters));
        this.contents.get(key).update(parameters);
      }
      this.droplets.update(snapshot);
      this.puddles.update(snapshot.puddles || []);
    }

    /** Remove all fluid overlays and restore the original imported content. */
    destroy() {
      this.contents.forEach(contents => contents.destroy());
      this.contents.clear();
      this.droplets.destroy();
      this.puddles.destroy();
    }
  }

  return {buildSurface, create: (three, worldRoot, getObject) => new LiquidView(three, worldRoot, getObject)};
});

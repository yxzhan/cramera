/* Measured balance readings in the authored laboratory display. */
(function (global, factory) {
  'use strict';
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.LaboratoryScale = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';

  // %% authored display replacement
  const DISPLAY_NAME = 'laboratory-scale-reading';
  const HOLDER_NAME = 'laboratory-scale-holder';
  const AUTHORED_DIGITS = new Set(['Balance digits', 'Balance_digits']);
  const CANVAS_SIZE = Object.freeze([640, 112]);

  /** Replace static lettering with observed mass while retaining the scale housing. */
  class Display {
    constructor(THREE, root, config, document) {
      this.root = root;
      this.hidden = new Map();
      this.canvas = document.createElement('canvas');
      [this.canvas.width, this.canvas.height] = CANVAS_SIZE;
      this.context = this.canvas.getContext('2d');
      this.texture = new THREE.CanvasTexture(this.canvas);
      this.texture.encoding = THREE.sRGBEncoding;
      this.mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(...config.displaySize),
        new THREE.MeshBasicMaterial({map: this.texture, toneMapped: false}),
      );
      this.mesh.name = DISPLAY_NAME;
      this.mesh.position.fromArray(config.displayPosition);
      this.mesh.rotation.x = Math.PI / 2;
      this.mesh.visible = false;
      root.add(this.mesh);
      this.lastLabel = null;
      this.holder = config.holder ? new Holder(THREE, root, config) : null;
    }

    /** Display only the server reading, including an explicitly unavailable state. */
    update(snapshot) {
      this.root.traverse(node => {
        if ((AUTHORED_DIGITS.has(node.name) || AUTHORED_DIGITS.has(node.userData.name)) && !this.hidden.has(node)) {
          this.hidden.set(node, node.visible);
          node.visible = false;
        }
      });
      this.mesh.visible = this.hidden.size > 0;
      const available = snapshot && snapshot.available && Number.isFinite(snapshot.grams);
      const value = available ? (Math.abs(snapshot.grams) < .0005 ? 0 : snapshot.grams).toFixed(3) + ' g' : '— g';
      const label = value + Boolean(available && snapshot.stable);
      if (label === this.lastLabel) return;
      this.lastLabel = label;
      const context = this.context;
      context.fillStyle = '#142729';
      context.fillRect(0, 0, ...CANVAS_SIZE);
      context.fillStyle = available && snapshot.stable ? '#bfe9d3' : '#edd4a1';
      context.font = '76px monospace';
      context.textAlign = 'right';
      context.textBaseline = 'middle';
      context.fillText(value, CANVAS_SIZE[0] - 20, CANVAS_SIZE[1] / 2, CANVAS_SIZE[0] - 40);
      this.texture.needsUpdate = true;
    }

    /** Restore authored visibility and release the display's renderer resources. */
    destroy() {
      for (const [node, visible] of this.hidden) node.visible = visible;
      this.hidden.clear();
      this.root.remove(this.mesh);
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
      this.texture.dispose();
      if (this.holder) this.holder.destroy();
    }
  }

  /** Show the collar's authored contact segments with an open centre and top. */
  class Holder {
    constructor(THREE, root, config) {
      const dimensions = config.holder;
      const radius = (dimensions.innerRadius + dimensions.outerRadius) / 2;
      this.root = root;
      this.group = new THREE.Group();
      this.group.name = HOLDER_NAME;
      this.group.position.fromArray(config.panPosition);
      this.geometry = new THREE.BoxGeometry(
        dimensions.outerRadius - dimensions.innerRadius,
        2 * dimensions.outerRadius * Math.tan(Math.PI / dimensions.segments),
        dimensions.height,
      );
      this.material = new THREE.MeshStandardMaterial({color: 0xb8c4c4, metalness: .85, roughness: .3});
      for (let index = 0; index < dimensions.segments; index += 1) {
        const angle = 2 * Math.PI * index / dimensions.segments;
        const wall = new THREE.Mesh(this.geometry, this.material);
        wall.position.set(radius * Math.cos(angle), radius * Math.sin(angle), dimensions.height / 2);
        wall.rotation.z = angle;
        wall.castShadow = true;
        wall.receiveShadow = true;
        this.group.add(wall);
      }
      root.add(this.group);
    }

    /** Remove the visible collar and release its shared geometry and material. */
    destroy() {
      this.root.remove(this.group);
      this.geometry.dispose();
      this.material.dispose();
    }
  }

  return {DISPLAY_NAME, HOLDER_NAME, create: (THREE, root, config, document) => new Display(THREE, root, config, document)};
});

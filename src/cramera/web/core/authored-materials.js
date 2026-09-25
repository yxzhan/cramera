/* Preserve imported physical materials while supporting shadows and selection. */
(function (global) {
  'use strict';

  // %% authored appearance
  const emissionByMaterial = new WeakMap();
  const HIGHLIGHT_COLOR = 0x39d5c8;

  class AuthoredMaterials {
    /** Keep authored emission available after temporary selection highlights. */
    static prepareMesh(mesh) {
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      // Shadow maps cannot transmit light through glass; mixed meshes retain the
      // shadow of their opaque surfaces, so glass should be exported separately.
      mesh.castShadow = materials.some(function (material) {
        return !material || !(material.transmission > 0);
      });
      mesh.receiveShadow = true;
      mesh.userData.preserveMaterials = true;
      materials.forEach(function (material) {
        // The r128 physical shader expresses transmission through fragment alpha;
        // its GLTF loader does not enable blending for opaque transmission assets.
        if (material && material.transmission > 0 && typeof THREE !== 'undefined' && THREE.REVISION === '128') {
          material.transparent = true;
          material.depthWrite = false;
          material.needsUpdate = true;
        }
        if (!material || !material.emissive || emissionByMaterial.has(material)) return;
        emissionByMaterial.set(material, {
          color: material.emissive.clone(), intensity: material.emissiveIntensity,
        });
      });
    }

    /** Highlight a preserved mesh, restoring its original emission on deselection. */
    static highlightMesh(mesh, on, intensity) {
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      materials.forEach(function (material) {
        const emission = material && emissionByMaterial.get(material);
        if (!emission) return;
        if (on) {
          material.emissive.setHex(HIGHLIGHT_COLOR);
          material.emissiveIntensity = intensity;
          return;
        }
        material.emissive.copy(emission.color);
        material.emissiveIntensity = emission.intensity;
      });
    }
  }

  global.AuthoredMaterials = AuthoredMaterials;
})(window);

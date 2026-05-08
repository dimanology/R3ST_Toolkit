/*:
 * @target MZ
 * @plugindesc R3ST Scene — renderingGroup assignment + per-group projection
 * switching for mz3d. Replaces MZ3D_RenderGroup.js + MZ3D_OrthoGroups.js.
 * Place BELOW mz3d.js in the Plugin Manager.
 * @author Dimanology
 *
 * @param enabled
 * @text Ortho/Persp Switching Enabled
 * @type boolean
 * @default true
 * @desc Master toggle for per-group projection switching.
 * Disable to revert to standard mz3d perspective rendering.
 *
 * @param perspectiveGroups
 * @text Perspective Rendering Groups
 * @type string
 * @default 2
 * @desc Comma-separated renderingGroupIds that use perspective projection.
 * All other groups use orthographic (no parallax). Default "2".
 *
 * @param camBoundDummyId
 * @text Camera Follow — Dummy Event ID
 * @type number
 * @min 1
 * @default 2
 * @desc Event ID of the dummy event used as mz3d camera target for smooth
 * following and boundary clamping. Must match @e<N> in your mz3d camera
 * config. Set to 0 to disable camera following entirely.
 *
 * @help r3st_toolkit.js — R3ST Scene plugin for mz3d
 * ════════════════════════════════════════════════════════════════════
 *
 * RENDERING GROUPS
 * ────────────────
 * Babylon.js renders meshes in renderingGroupId order (0 → 1 → 2 → 3),
 * clearing the depth buffer between each group. Higher group = always on top.
 *
 *   Group 0 → ORTHO  ← background geometry (BG.glb, R3ST_Room.obj)
 *   Group 1 → ORTHO  ← default: tiles, walls (mz3d default)
 *   Group 2 → PERSP  ← characters, player
 *   Group 3 → ORTHO  ← foreground overlays (fg.glb)
 *
 * TAGGING CHARACTERS / EVENTS
 * ───────────────────────────
 * In Event Note or page Comment:
 *   <mv3d: renderGroup(2)>
 *
 * For player / followers — in Database > Actors > Note:
 *   <mv3d: renderGroup(2)>
 *
 * TAGGING TILE DOODADS
 * ────────────────────
 * In map note <mz3d-tiles> block:
 *   B,2,31:model(fg.glb),renderGroup(3),climb(false)
 *   C,0,0:model(BG.glb),renderGroup(0),climb(false)
 *
 * PROJECTION SWITCHING
 * ────────────────────
 * Before each renderingGroup draws, the camera switches between
 * ORTHOGRAPHIC and PERSPECTIVE. The ortho frustum is derived from
 * the current camera dist + fov, so objects at the focal plane appear
 * identical in both modes — only parallax behaviour differs.
 *
 * SCRIPT CALLS
 * ────────────
 *   R3ST_Scene.enable()    — activate projection switching at runtime
 *   R3ST_Scene.disable()   — deactivate, restore full perspective
 *   R3ST_Scene.isEnabled   — boolean
 */

(() => {
  'use strict';

  const TAG = '[R3ST]';

  if (!window.mz3d) {
    console.error(TAG, 'mz3d not found. Place this plugin BELOW mz3d.js.');
    return;
  }

  const mz3d    = window.mz3d;
  const BABYLON = window.BABYLON;

  // ── Parameters ────────────────────────────────────────────────────────────

  const params          = PluginManager.parameters('r3st_toolkit');
  const PARAM_ENABLED   = params.enabled !== 'false';
  const CAM_BOUND_EVENT = Number(params.camBoundDummyId) || 2;

  const PERSP_GROUPS = new Set(
    (params.perspectiveGroups || '2')
      .split(',')
      .map(s => parseInt(s.trim()))
      .filter(n => !isNaN(n))
  );

  // ── State ─────────────────────────────────────────────────────────────────

  let active      = false;
  let initialized = false;
  let _beforeGroupObs = null;
  let _afterRenderObs = null;

  // ── Shared: value parser ──────────────────────────────────────────────────

  function parseRGValue(v) {
    const n = parseInt(v);
    if (isNaN(n) || n < 0 || n > 3) return null;
    return n;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  RENDER GROUP ASSIGNMENT
  // ═══════════════════════════════════════════════════════════════════════════

  // Tile doodad config: B,2,31:model(fg.glb),renderGroup(3)
  mz3d.tilesetConfigurationFunctions.rendergroup = function(conf, n) {
    const rg = parseRGValue(n);
    if (rg != null) conf.rendergroup = rg;
  };

  // Set renderingGroupId on a node and all descendant meshes (deep traversal).
  // model.mesh may be a TransformNode (root of a GLB hierarchy), so we check
  // for renderingGroupId support before assigning, and recurse into all children.
  function setGroupRecursive(node, rg) {
    if (node.renderingGroupId !== undefined) {
      node.renderingGroupId = rg;
    }
    // getChildMeshes(false) = all descendants, not just direct children
    if (node.getChildMeshes) {
      node.getChildMeshes(false).forEach(child => { child.renderingGroupId = rg; });
    }
  }

  // Parse <mv3d: renderGroup(N)> or <mz3d: renderGroup(N)> from a string
  function parseRenderGroup(text) {
    if (!text) return null;
    const match = text.match(/<m[vz]3d[:\s]+rendergroup\s*\(\s*(\w+)\s*\)/i);
    if (!match) return null;
    return parseRGValue(match[1]);
  }

  function getPageComments(sprite) {
    const page = sprite.char?.page?.();
    if (!page) return '';
    return page.list
      .filter(cmd => cmd.code === 108 || cmd.code === 408)
      .map(cmd => cmd.parameters[0])
      .join('\n');
  }

  function getActorNote(sprite) {
    if (!sprite.isPlayer && !sprite.isFollower) return '';
    const memberIndex = sprite.isFollower ? sprite.char._memberIndex : 0;
    const actorId = $gameParty?._actors?.[memberIndex];
    if (!actorId) return '';
    return $dataActors[actorId]?.note || '';
  }

  function getRenderGroup(sprite) {
    const note     = sprite.char?.event?.()?.note || getActorNote(sprite);
    const comments = getPageComments(sprite);
    return parseRenderGroup(note) ?? parseRenderGroup(comments);
  }

  // Apply renderingGroupId to a character sprite's mesh.
  // silent=true skips the console log (used for per-frame reapply).
  function applyRenderGroup(sprite, silent) {
    const rg = getRenderGroup(sprite);
    if (rg == null) return;
    const mesh = sprite.model?.mesh;
    if (!mesh) return;
    setGroupRecursive(mesh, rg);
    if (!silent) {
      const label = sprite.char?.event?.()?.name
        ?? (sprite.isPlayer ? '(player)' : sprite.isFollower ? '(follower)' : '(vehicle)');
      console.log(TAG, `Applied renderingGroupId=${rg} →`, label);
    }
  }

  function applyToAll(silent) {
    if (!mz3d.characters) return;
    for (const sprite of mz3d.characters) {
      applyRenderGroup(sprite, silent);
    }
  }

  function applyRenderGroupToDoodad(doodad) {
    const rg = doodad?.conf?.rendergroup;
    if (rg == null) return;
    const mesh = doodad.mesh;
    if (!mesh) return;
    setGroupRecursive(mesh, rg);
    console.log(TAG, `[tile] Applied renderingGroupId=${rg}`);
  }

  function applyToAllDoodads() {
    if (!mz3d.cells) return;
    for (const cell of Object.values(mz3d.cells)) {
      if (!cell.doodads) continue;
      for (let i = 0; i < cell.doodads.length; i++) {
        applyRenderGroupToDoodad(cell.doodads[i]);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  ORTHO / PERSP PROJECTION SWITCHING
  // ═══════════════════════════════════════════════════════════════════════════

  // Babylon.js 5 (WebGL2) uploads viewProjection into a GPU Uniform Buffer
  // Object (scene._sceneUbo) once per camera pass. Per-group projection
  // switching requires pushing new matrices into that UBO before each group
  // renders. Both VP matrices are pre-computed at frame start so the camera
  // state is never dirty at draw time.
  //
  // scene.getTransformMatrix() is also patched as a fallback for non-UBO
  // rendering paths (sprites, screen-space overlays, mz3d's own projections).

  let _orthoTransform        = null; // lazy-initialized on first frame
  let _perspTransform        = null;
  let _orthoProjection       = null; // separate projection matrices for UBO
  let _perspProjection       = null;
  let _transformsReady       = false;
  let _currentRenderGroup    = -1;   // -1 = not inside a group render
  let _origGetTransformMatrix = null;

  // Compute orthoLeft/Right/Top/Bottom matching the persp frustum at dist.
  function applyOrthoFrustum() {
    const cam = mz3d.camera;
    let dist = mz3d.blendCameraDist.currentValue();
    if (dist < 0.1) {
      const fc = window._mz3dFlyCam;
      dist = (fc && fc.effectiveDist > 0.1) ? fc.effectiveDist
           : mz3d.getDistForFov();
    }
    const halfH = dist * Math.tan(cam.fov / 2);
    const halfW = halfH * mz3d.engine.getAspectRatio(cam);
    cam.orthoLeft   = -halfW;
    cam.orthoRight  =  halfW;
    cam.orthoTop    =  halfH;
    cam.orthoBottom = -halfH;
  }

  // Called once per frame (onBeforeRenderObservable).
  // Temporarily switches camera state to compute both VP matrices,
  // then fully restores the camera to its original state.
  function precomputeTransforms() {
    const cam  = mz3d.camera;
    const view = cam.getViewMatrix();

    // Lazy-init: clone real Matrices from the scene so we get the right type
    if (!_orthoTransform) {
      const ref = mz3d.scene.getTransformMatrix();
      _orthoTransform  = ref.clone();
      _perspTransform  = ref.clone();
      _orthoProjection = ref.clone();
      _perspProjection = ref.clone();
    }

    // Save full camera projection state
    const savedMode   = cam.mode;
    const savedLeft   = cam.orthoLeft;
    const savedRight  = cam.orthoRight;
    const savedTop    = cam.orthoTop;
    const savedBottom = cam.orthoBottom;

    // Perspective VP + projection
    cam.mode = BABYLON.Camera.PERSPECTIVE_CAMERA;
    cam.getProjectionMatrix(true);
    _perspProjection.copyFrom(cam._projectionMatrix);
    view.multiplyToRef(cam._projectionMatrix, _perspTransform);

    // Ortho VP + projection (frustum matched to persp at camera dist)
    cam.mode = BABYLON.Camera.ORTHOGRAPHIC_CAMERA;
    applyOrthoFrustum();
    cam.getProjectionMatrix(true);
    _orthoProjection.copyFrom(cam._projectionMatrix);
    view.multiplyToRef(cam._projectionMatrix, _orthoTransform);

    // Fully restore camera state
    cam.mode         = savedMode;
    cam.orthoLeft    = savedLeft;
    cam.orthoRight   = savedRight;
    cam.orthoTop     = savedTop;
    cam.orthoBottom  = savedBottom;
    cam.getProjectionMatrix(true);

    _transformsReady = true;
  }

  // ── Scene UBO ────────────────────────────────────────────────────────────
  // Babylon.js 5 (WebGL2) uploads viewProjection into a GPU Uniform Buffer
  // Object once per camera pass. Per-group projection switching requires
  // updating that UBO directly before each group renders.
  //
  // The backing property is mangled in the bundle, so we scan the scene
  // object at enable time for an object that has the UniformBuffer API.

  let _sceneUbo = null;

  function findSceneUbo() {
    // Try the standard (non-mangled) name first
    if (mz3d.scene._sceneUbo &&
        typeof mz3d.scene._sceneUbo.updateMatrix === 'function') {
      console.log(TAG, 'Scene UBO found at scene._sceneUbo');
      return mz3d.scene._sceneUbo;
    }
    // Scan all own properties for an object with the UniformBuffer API
    for (const key of Object.keys(mz3d.scene)) {
      const val = mz3d.scene[key];
      if (val && typeof val === 'object'
          && typeof val.updateMatrix  === 'function'
          && typeof val.update        === 'function'
          && typeof val.bindToEffect  === 'function') {
        console.log(TAG, `Scene UBO found at scene.${key}`);
        return val;
      }
    }
    console.warn(TAG, 'Scene UBO not found — falling back to getTransformMatrix hook');
    return null;
  }

  // Patch scene.getTransformMatrix() to return the correct VP matrix
  // for whichever rendering group is currently drawing.
  // Outside group rendering (_currentRenderGroup === -1), defer to the
  // original method so mz3d's screen-space calculations are unaffected.
  // (Fallback when UBO cannot be found/updated directly.)
  function installTransformHook() {
    _origGetTransformMatrix = mz3d.scene.getTransformMatrix.bind(mz3d.scene);
    mz3d.scene.getTransformMatrix = function() {
      if (!_transformsReady || _currentRenderGroup === -1) {
        return _origGetTransformMatrix();
      }
      return PERSP_GROUPS.has(_currentRenderGroup) ? _perspTransform : _orthoTransform;
    };
  }

  function uninstallTransformHook() {
    if (_origGetTransformMatrix) {
      mz3d.scene.getTransformMatrix = _origGetTransformMatrix;
      _origGetTransformMatrix = null;
    }
    _transformsReady    = false;
    _currentRenderGroup = -1;
  }

  function onBeforeGroup(info) {
    _currentRenderGroup = info.renderingGroupId;
    // Re-apply character renderingGroupIds before each group draws.
    applyToAll(true);

    if (!_transformsReady) return;
    const isPersp  = PERSP_GROUPS.has(_currentRenderGroup);
    const vp   = isPersp ? _perspTransform  : _orthoTransform;
    const proj = isPersp ? _perspProjection : _orthoProjection;

    // Update the GPU scene UBO so all shaders in this group use the correct
    // viewProjection. Also update separate view/projection in case shaders
    // use them individually (lighting, depth reconstruction, etc.).
    if (_sceneUbo) {
      _sceneUbo.updateMatrix('viewProjection', vp);
      _sceneUbo.updateMatrix('projection', proj);
      _sceneUbo.updateMatrix('view', mz3d.camera.getViewMatrix());
      _sceneUbo.update();
    }
    // Fallback: the getTransformMatrix hook handles non-UBO paths
  }

  // After the full frame, restore the persp VP so mz3d's inter-frame
  // screen-space projections (Vector3.Project etc.) are unaffected.
  function onAfterRender() {
    _currentRenderGroup = -1;
    if (_sceneUbo && _transformsReady) {
      _sceneUbo.updateMatrix('viewProjection', _perspTransform);
      _sceneUbo.updateMatrix('projection', _perspProjection);
      _sceneUbo.updateMatrix('view', mz3d.camera.getViewMatrix());
      _sceneUbo.update();
    }
    mz3d.camera.mode = BABYLON.Camera.PERSPECTIVE_CAMERA;
  }

  // ── Enable / disable ──────────────────────────────────────────────────────

  let _beforeRenderObs = null;

  function enable() {
    if (active) return;
    if (!initialized) {
      console.warn(TAG, 'Cannot enable before mz3d setup completes.');
      return;
    }
    _sceneUbo = findSceneUbo();
    installTransformHook();
    _beforeRenderObs = mz3d.scene.onBeforeRenderObservable.add(precomputeTransforms);
    _beforeGroupObs  = mz3d.scene.onBeforeRenderingGroupObservable.add(onBeforeGroup);
    _afterRenderObs  = mz3d.scene.onAfterRenderObservable.add(onAfterRender);
    active = true;
    console.log(TAG, 'Enabled. Perspective groups:', [...PERSP_GROUPS].join(', '));
  }

  function disable() {
    if (!active) return;
    uninstallTransformHook();
    mz3d.scene.onBeforeRenderObservable.remove(_beforeRenderObs);
    mz3d.scene.onBeforeRenderingGroupObservable.remove(_beforeGroupObs);
    mz3d.scene.onAfterRenderObservable.remove(_afterRenderObs);
    _beforeRenderObs = null;
    _beforeGroupObs  = null;
    _afterRenderObs  = null;
    if (mz3d.camera) mz3d.camera.mode = BABYLON.Camera.PERSPECTIVE_CAMERA;
    _sceneUbo = null;
    active = false;
    console.log(TAG, 'Disabled.');
  }

  // ── Feature hook ──────────────────────────────────────────────────────────

  new mz3d.Feature('r3stScene', {
    setup() {
      initialized = true;
      if (PARAM_ENABLED) enable();
    },
    // Initial apply when mz3d first configures a character mesh.
    // Note: mz3d may override renderingGroupId after this fires.
    // The onBeforeGroup id=0 re-apply is the authoritative fix.
    configureChar(sprite) {
      applyRenderGroup(sprite, false);
    },
    updateCameraMode() {
      applyToAll(false);
      applyToAllDoodads();
    },
  });

  // ═══════════════════════════════════════════════════════════════════════════
  //  CAMERA BOUNDARY  (replaces mz3d_smoothclamp.js)
  // ═══════════════════════════════════════════════════════════════════════════
  //
  // Reads <r3st_cam_border>left,right,top,bottom</r3st_cam_border> from the
  // map note on each map load.  The tag is written by the R3ST Blender add-on
  // when the camera is exported, so every map carries its own border values.
  //
  // A dummy event (fixed position, Through ON, no graphic) is used as the mz3d
  // camera target (@e<camBoundDummyId> in the map note / mz3d config).  Every
  // frame the dummy's position is clamped to the player's position within the
  // boundary rectangle, creating the smooth "camera stops at map edges" effect.
  //
  // Setup (same as the old smoothclamp.js):
  //   1. Create a dummy event on every map — Through ON, Fixed, no graphic.
  //   2. Set its event ID to match the "Camera Follow — Dummy Event ID" param.
  //   3. In the mz3d map note add:  camera(yaw,pitch|dist|height|@e<ID>)
  //      or set mz3d's camera target to @e<ID> however your version supports it.
  //   4. Export camera from Blender with Camera Boundary enabled → the
  //      <r3st_cam_border> tag is written to MapXXX.json automatically.
  //
  // If no <r3st_cam_border> tag is found the dummy event is still synced to the
  // player (smooth follow, no clamping).  Set camBoundDummyId to 0 to disable.

  let _camBorder = null;   // null until a map with a border tag is loaded

  /** Parse <r3st_cam_border>L,R,T,B</r3st_cam_border> from a note string. */
  function parseCamBorder(note) {
    if (!note) return null;
    const m = note.match(/<r3st_cam_border>([\d.\s,]+)<\/r3st_cam_border>/i);
    if (!m) return null;
    const parts = m[1].split(',').map(Number);
    if (parts.length < 4 || parts.some(isNaN)) return null;
    return { left: parts[0], right: parts[1], top: parts[2], bottom: parts[3] };
  }

  // Per-frame hook: sync dummy event position to clamped player position.
  if (CAM_BOUND_EVENT > 0) {
    const _origUpdateMain = Scene_Map.prototype.updateMain;
    Scene_Map.prototype.updateMain = function () {
      _origUpdateMain.call(this);

      if (!$gameMap || !$gamePlayer) return;
      const dummy = $gameMap.event(CAM_BOUND_EVENT);
      if (!dummy) return;

      let clampedX = $gamePlayer._realX;
      let clampedY = $gamePlayer._realY;

      if (_camBorder) {
        const mapW = $dataMap.width;
        const mapH = $dataMap.height;
        // RPG Maker: X increases east, Y increases south (Y=0 = north edge).
        clampedX = Math.max(_camBorder.left,
                   Math.min(mapW - _camBorder.right,  clampedX));
        clampedY = Math.max(_camBorder.top,
                   Math.min(mapH - _camBorder.bottom, clampedY));
      }

      dummy._x     = clampedX;
      dummy._y     = clampedY;
      dummy._realX = clampedX;
      dummy._realY = clampedY;
    };
  }

  // ── Map load fallback ─────────────────────────────────────────────────────

  const _onMapLoaded = Scene_Map.prototype.onMapLoaded;
  Scene_Map.prototype.onMapLoaded = function () {
    _onMapLoaded.apply(this, arguments);

    // Parse per-map camera boundary tag written by the R3ST Blender add-on.
    _camBorder = parseCamBorder($dataMap?.note || '');
    if (_camBorder) {
      console.log(TAG, `Camera border: L=${_camBorder.left} R=${_camBorder.right}`
                     + ` T=${_camBorder.top} B=${_camBorder.bottom}`);
    }

    setTimeout(() => {
      applyToAll(false);
      applyToAllDoodads();
    }, 500);
  };

  // ── Public API ────────────────────────────────────────────────────────────

  window.R3ST_Scene = {
    enable,
    disable,
    get isEnabled() { return active; },
    get camBorder()  { return _camBorder; },
  };

  console.log(TAG, 'Ready. Ortho switching:', PARAM_ENABLED ? 'ON' : 'OFF');
})();

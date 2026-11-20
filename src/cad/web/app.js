/* Live 3D viewport for `cad serve`.
 *
 * One page, no build step.  three.js is vendored under /static/vendor; every
 * byte of UI state comes from /api/state, the SSE stream and the binary mesh
 * endpoints.  Sections below: utilities, binary mesh parsing, scene, geometry,
 * picking, section planes, panels, timeline, copy-context, SSE and boot.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';

// --------------------------------------------------------------------- constants

const PALETTE = ['#9eb0c7', '#d99e6b', '#8cbf8c', '#cc8ca6', '#bfb880', '#8ca6d9'];
const REFERENCE_COLOR = '#dbd6b8';
const EDGE_COLOR = 0x0b0e12;
const BG_COLOR = 0x10141a;
const WORKSPACE_PATH = window.location.pathname.match(/^\/(?:projects|workspace)\/[^/]+/)?.[0] ?? '';
const apiPath = (path) => WORKSPACE_PATH + path;

const COMPACT_PANELS_QUERY = '(max-width: 960px)';

// eye = direction from the target toward the camera, up = screen up
const VIEWS = {
  iso:    { eye: [1, -1, 1],  up: [0, 0, 1] },
  front:  { eye: [0, -1, 0],  up: [0, 0, 1] },
  back:   { eye: [0, 1, 0],   up: [0, 0, 1] },
  left:   { eye: [-1, 0, 0],  up: [0, 0, 1] },
  right:  { eye: [1, 0, 0],   up: [0, 0, 1] },
  top:    { eye: [0, 0, 1],   up: [0, 1, 0] },
  bottom: { eye: [0, 0, -1],  up: [0, -1, 0] },
};
const VIEW_KEYS = ['iso', 'front', 'back', 'left', 'right', 'top', 'bottom'];
const NUMBER_KEYS = ['1', '2', '3', '4', '5', '6', '7'];
const FIELD_LABELS = {
  stress: 'von Mises stress',
  displacement: 'displacement',
  layers: 'layer tension',
  safety: 'safety factor',
};
const PRINT_COLLAPSED_KEY = 'cad.print.collapsed';

// --------------------------------------------------------------------- utilities

const byId = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value));
}

function own(obj, name) {
  return Object.prototype.hasOwnProperty.call(obj || {}, name);
}

function group(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function fmt(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return '-';
  const n = Number(value);
  const d = Math.abs(n) >= 100 ? Math.min(digits, 1) : digits;
  return group(Number(n.toFixed(d)));
}

function fmtInt(value) {
  if (value == null || !Number.isFinite(Number(value))) return '-';
  return group(Math.round(Number(value)));
}

function fmtNumber(value, digits) {
  if (value == null || !Number.isFinite(Number(value))) return '-';
  const fixed = Number(value).toFixed(digits);
  if (!fixed.includes('.')) return fixed;
  return fixed.replace(/0+$/, '').replace(/\.$/, '');
}

function fmtPoint(point) {
  if (!Array.isArray(point) || point.length < 3) return '-';
  return point.slice(0, 3).map((v) => fmt(v, 1)).join(', ');
}

function niceNumber(x) {
  if (!(x > 0) || !Number.isFinite(x)) return 1;
  const exp = Math.floor(Math.log10(x));
  const f = x / 10 ** exp;
  const nice = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  return nice * 10 ** exp;
}

function sanitizeName(name) {
  return String(name || 'cad').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'cad';
}

function formatScalar(value) {
  if (value == null) return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function colourFor(part, index) {
  if (part && part.reference) return REFERENCE_COLOR;
  const raw = part && part.color;
  return typeof raw === 'string' && /^#[0-9a-f]{6}$/i.test(raw) ? raw : PALETTE[index % PALETTE.length];
}

function errorKey(error) {
  if (!error) return '';
  return `${error.code || ''}|${error.message || ''}`;
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload == null ? {} : payload),
  });
  let body = null;
  try {
    body = await response.json();
  } catch { /* some routes return plain text on failure */ }
  if (!response.ok || !body || body.ok === false) {
    const error = (body && body.error) || null;
    const message = (error && (error.message || error.code))
      || (response.status === 404 ? `${path} is not available (HTTP 404)` : `HTTP ${response.status}`);
    const failure = new Error(message);
    failure.code = (error && error.code) || `HTTP_${response.status}`;
    failure.status = response.status;
    failure.details = error && error.details;
    throw failure;
  }
  return body;
}

function locationText(error) {
  const location = error && error.details && error.details.location;
  if (!location) return '';
  const file = String(location.file || '?').split('/').pop();
  const line = location.line != null ? `:${location.line}` : '';
  return `${file}${line}`;
}

// --------------------------------------------------------------------- colormaps

const TURBO_R = [0.13572138, 4.6153926, -42.66032258, 132.13108234, -152.94239396, 59.28637943];
const TURBO_G = [0.09140261, 2.19418839, 4.84296658, -14.18503333, 4.27729857, 2.82956604];
const TURBO_B = [0.1066733, 12.64194608, -60.58204836, 110.36276771, -89.90310912, 27.34824973];
let turboGradientCache = null;

function turboColor(t) {
  const x = clamp(t, 0, 1);
  const powers = [1, x, x * x, x ** 3, x ** 4, x ** 5];
  const channel = (coeff) => Math.round(
    255 * clamp(coeff.reduce((sum, c, i) => sum + c * powers[i], 0), 0, 1),
  );
  return [channel(TURBO_R), channel(TURBO_G), channel(TURBO_B)];
}

function turboGradient() {
  if (turboGradientCache) return turboGradientCache;
  const stops = [];
  for (let i = 0; i <= 10; i += 1) {
    const t = i / 10;
    const [r, g, b] = turboColor(t);
    stops.push(`rgb(${r}, ${g}, ${b}) ${Math.round(t * 100)}%`);
  }
  turboGradientCache = `linear-gradient(90deg, ${stops.join(', ')})`;
  return turboGradientCache;
}

function cssTriple(triple) {
  if (!Array.isArray(triple) || triple.length < 3) return '#777';
  const [r, g, b] = triple.slice(0, 3).map((v) => clamp(v > 1 ? v / 255 : v, 0, 1));
  return `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
}

// --------------------------------------------------------------------- binary mesh

/* Header JSON, then positions, normals, indices, face ids, edge lens, edge
 * points and colours.  Nothing is padded, so walk a byte cursor and copy only
 * when a typed array would land off its natural alignment. */

function parseMesh(buffer, byteOffset = 0, byteLength = buffer.byteLength) {
  const bytes = new Uint8Array(buffer, byteOffset, byteLength);
  if (bytes.byteLength < 8) throw new Error('mesh payload is too short');
  const headerLen = new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0, true);
  if (headerLen <= 0 || 4 + headerLen > bytes.byteLength) throw new Error('mesh header is corrupt');
  const header = JSON.parse(new TextDecoder('utf-8').decode(bytes.subarray(4, 4 + headerLen)));
  const count = (name) => {
    const value = Number(header[name]);
    if (!Number.isInteger(value) || value < 0) throw new Error(`mesh header ${name} is invalid`);
    return value;
  };
  const vertices = count('vertices');
  const triangles = count('triangles');
  const edges = count('edges');
  const edgePoints = count('edge_points');
  const colours = count('colors');

  let cursor = 4 + headerLen;
  const take = (Type, length) => {
    const wanted = length * Type.BYTES_PER_ELEMENT;
    if (cursor + wanted > bytes.byteLength) throw new Error('mesh payload is truncated');
    const base = bytes.byteOffset + cursor;
    let out;
    if (base % Type.BYTES_PER_ELEMENT === 0) {
      out = new Type(bytes.buffer, base, length);
    } else {
      out = new Type(bytes.slice(cursor, cursor + wanted).buffer, 0, length);
    }
    cursor += wanted;
    return out;
  };

  const positions = take(Float32Array, vertices * 3);
  const normals = take(Float32Array, vertices * 3);
  const indices = take(Uint32Array, triangles * 3);
  const faceIds = take(Int32Array, triangles);
  const edgeLens = take(Uint32Array, edges);
  const edgePointData = take(Float32Array, edgePoints * 3);
  const colors = take(Float32Array, colours * 3);

  return {
    header, positions, normals, indices, faceIds, edgeLens,
    edgePoints: edgePointData, colors, bytesRead: cursor, byteLength: bytes.byteLength,
  };
}

function edgeSegments(parsed) {
  let pairs = 0;
  for (let i = 0; i < parsed.edgeLens.length; i += 1) pairs += Math.max(0, parsed.edgeLens[i] - 1);
  if (!pairs) return new Float32Array(0);
  const out = new Float32Array(pairs * 6);
  const points = parsed.edgePoints;
  let offset = 0;
  let point = 0;
  for (let i = 0; i < parsed.edgeLens.length; i += 1) {
    const length = parsed.edgeLens[i];
    for (let k = 0; k + 1 < length; k += 1) {
      const a = (point + k) * 3;
      const b = a + 3;
      out[offset] = points[a];
      out[offset + 1] = points[a + 1];
      out[offset + 2] = points[a + 2];
      out[offset + 3] = points[b];
      out[offset + 4] = points[b + 1];
      out[offset + 5] = points[b + 2];
      offset += 6;
    }
    point += length;
  }
  return out;
}

// --------------------------------------------------------------------- store

const store = {
  doc: null,
  stateSeq: 0,
  stateTimer: 0,
  revision: null,
  viewRevision: null,
  loadedRevision: null,
  loadedPartsKey: '',
  geometrySeq: 0,
  geometryError: null,
  revisionGroups: new Map(),
  frameGroups: new Map(),
  frames: new Map(),
  frameEpoch: 0,
  frameSeq: 0,
  frameTimer: 0,
  visibility: new Map(),
  activePart: null,
  selection: null,
  featureFaceIds: null,
  get chatSelection() { return window.cadWorkspace.selection; },
  hover: null,
  hoverPoint: null,
  hoverScheduled: false,
  hoverTimer: 0,
  faceSeq: 0,
  hoverSeq: 0,
  selHighlight: null,
  hoverHighlight: null,
  faceTables: new Map(),
  bounds: null,
  didFirstFit: false,
  section: { on: false, axis: 'z', flip: false, min: -1, max: 1, value: 1, initialised: false },
  stressMode: false,
  fea: null,
  feaRunning: false,
  feaError: '',
  feaSeq: 0,
  study: null,
  feaField: 'stress',
  feaQuality: 'normal',
  timelinePart: null,
  timelineStep: 0,
  timelineSig: '',
  timelinePlaying: false,
  autoPlaying: false,
  autoPlayPending: false,
  playTimer: 0,
  building: false,
  eventBuilding: false,
  progress: null,
  notes: [],
  bannerError: null,
  dismissedError: '',
  emptyNotice: '',
  emptyError: '',
  emptyBusy: false,
  emptyProbe: { signature: '', older: [], running: false },
  source: null,
  conn: 'paused',
  paramPending: {},
  paramTimer: 0,
  pointerDown: null,
  copyTimer: 0,
  toastTimer: 0,
  print: {
    contextKey: '',
    forPart: null,
    followPartMaterial: true,
    material: null,
    bed: null,
    bedTouched: false,
    custom: { x: 256, y: 256, z: 256 },
    customTouched: false,
    angle: 45,
    nozzle: 0.4,
    data: null,
    dataKey: '',
    dataPart: null,
    dataRevision: null,
    error: '',
    loading: false,
    seq: 0,
    timer: 0,
    promise: null,
    overlay: null,
    overlayKind: null,
    collapsed: false,
  },
  bedPreview: {
    active: false,
    part: null,
    faceId: null,
    ref: null,
    normal: null,
    prev: null,
    bed: null,
    error: '',
    busy: false,
    saving: false,
  },
  chat: {
    open: true,
    pendingChatDraft: '',
  },
};

// --------------------------------------------------------------------- scene

const view = {
  renderer: null,
  backend: 'pending',
  webgpu: null,
  gpuError: '',
  scene: null,
  persp: null,
  ortho: null,
  camera: null,
  controls: null,
  canvas: null,
  clipRoot: null,
  mode: 'persp',
  orthoHalf: 100,
  gizmoScene: null,
  gizmoCamera: null,
  gizmoDir: new THREE.Vector3(),
  ground: null,
  materials: new Set(),
  clipPlane: new THREE.Plane(new THREE.Vector3(0, 0, -1), 1),
  raycaster: new THREE.Raycaster(),
  sceneRadius: 100,
  lastPaint: 0,
};

function withTimeout(promise, ms, label) {
  let timer = 0;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

function replaceViewportCanvas() {
  const old = byId('gl');
  if (!old) return;
  const fresh = document.createElement('canvas');
  fresh.id = 'gl';
  fresh.className = old.className;
  fresh.setAttribute('aria-label', old.getAttribute('aria-label') || '3D viewport');
  fresh.tabIndex = old.tabIndex >= 0 ? old.tabIndex : 0;
  old.replaceWith(fresh);
  view.canvas = fresh;
}

function configureRenderer(renderer, backend) {
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(BG_COLOR, 1);
  renderer.autoClear = false;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.02;
  if (renderer.shadowMap) renderer.shadowMap.enabled = false;
  if (backend === 'webgl2') renderer.localClippingEnabled = true;
}

function setGpuStatus() {
  const node = byId('status-gpu');
  if (!node) return;
  if (view.backend === 'webgpu') {
    setText(node, 'gpu: webgpu');
    node.title = 'WebGPU renderer';
  } else if (view.backend === 'webgl2') {
    setText(node, 'gpu: webgl2');
    node.title = view.gpuError ? `WebGL2 fallback: ${view.gpuError}` : 'WebGL2 renderer';
  } else {
    setText(node, 'gpu: none');
    node.title = view.gpuError || 'no renderer';
  }
}

async function createRenderer() {
  const canvas = view.canvas;
  if (navigator.gpu) {
    try {
      const module = await withTimeout(import('three/webgpu'), 6000, 'WebGPU module');
      const renderer = new module.WebGPURenderer({ canvas, antialias: true, alpha: false });
      // Prefer our own WebGLRenderer fallback over three's internal WebGL backend.
      if (renderer._getFallback !== undefined) {
        renderer._getFallback = (error) => { throw error; };
      }
      await withTimeout(renderer.init(), 3500, 'WebGPU renderer');
      if (!(renderer.backend && renderer.backend.isWebGPUBackend === true)) {
        throw new Error('WebGPU backend was not selected');
      }
      view.webgpu = module;
      view.backend = 'webgpu';
      configureRenderer(renderer, 'webgpu');
      view.renderer = renderer;
      setGpuStatus();
      return;
    } catch (error) {
      view.gpuError = String(error && (error.message || error));
      console.warn('cad: WebGPU unavailable, using WebGL2 —', error);
      replaceViewportCanvas();
    }
  } else {
    view.gpuError = 'navigator.gpu is not available';
    console.info('cad: navigator.gpu is not available, using WebGL2');
  }

  const renderer = new THREE.WebGLRenderer({
    canvas: view.canvas, antialias: true, alpha: false, preserveDrawingBuffer: true,
  });
  view.backend = 'webgl2';
  configureRenderer(renderer, 'webgl2');
  view.renderer = renderer;
  setGpuStatus();
}

async function initScene() {
  view.canvas = byId('gl');

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG_COLOR);
  view.scene = scene;

  const persp = new THREE.PerspectiveCamera(42, 1, 0.1, 1e5);
  persp.up.set(0, 0, 1);
  persp.position.set(160, -220, 150);

  const ortho = new THREE.OrthographicCamera(-100, 100, 100, -100, 0.01, 1e6);
  ortho.up.set(0, 0, 1);
  ortho.position.copy(persp.position);

  view.persp = persp;
  view.ortho = ortho;
  view.camera = persp;

  scene.add(new THREE.HemisphereLight(0xdfe8f4, 0x15181d, 1.25));
  const key = new THREE.DirectionalLight(0xffffff, 2.1);
  key.position.set(0.55, -0.85, 1.1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xb9cbe0, 0.75);
  fill.position.set(-0.9, 0.55, 0.4);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.35);
  rim.position.set(0.1, 0.9, -0.6);
  scene.add(rim);

  buildGizmo();

  // A plain group is always valid; WebGPU replaces it with a ClippingGroup.
  const initialRoot = new THREE.Group();
  view.clipRoot = initialRoot;
  scene.add(initialRoot);

  await createRenderer();

  if (view.backend === 'webgpu' && view.webgpu) {
    const clipped = new view.webgpu.ClippingGroup();
    clipped.clipIntersection = false;
    for (const child of [...initialRoot.children]) clipped.add(child);
    scene.remove(initialRoot);
    view.clipRoot = clipped;
    scene.add(clipped);
  }

  attachControls();
  resize();
  window.addEventListener('resize', resize);
  if (window.ResizeObserver) new ResizeObserver(() => resize()).observe(byId('viewport'));
}

function buildGizmo() {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.up.set(0, 0, 1);

  const axes = new THREE.AxesHelper(1);
  axes.material.depthTest = false;
  axes.material.transparent = true;
  axes.material.opacity = 0.95;
  axes.renderOrder = 5;
  scene.add(axes);

  const labels = [
    ['X', '#d1685f', [1.38, 0, 0]],
    ['Y', '#74b07a', [0, 1.38, 0]],
    ['Z', '#6d93cb', [0, 0, 1.38]],
  ];
  for (const [text, color, position] of labels) scene.add(axisLabel(text, color, position));

  view.gizmoScene = scene;
  view.gizmoCamera = camera;
}

function axisLabel(text, color, position) {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 64, 64);
  ctx.fillStyle = color;
  ctx.font = '600 40px ui-monospace, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 32, 34);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.SpriteMaterial({
    map: texture, transparent: true, depthTest: false, depthWrite: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.position.set(position[0], position[1], position[2]);
  sprite.scale.set(0.5, 0.5, 1);
  sprite.renderOrder = 6;
  return sprite;
}

function attachControls() {
  if (view.controls) view.controls.dispose();
  const controls = new OrbitControls(view.camera, view.canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.screenSpacePanning = true;
  controls.rotateSpeed = 0.85;
  controls.zoomSpeed = 0.9;
  controls.panSpeed = 0.9;
  controls.minDistance = 1e-3;
  controls.maxDistance = 1e6;
  controls.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
  };
  controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
  view.controls = controls;
}

function resize() {
  const host = byId('viewport');
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  if (view.renderer) view.renderer.setSize(width, height, false);
  const aspect = width / height;
  view.persp.aspect = aspect;
  view.persp.updateProjectionMatrix();
  updateOrthoFrustum(aspect);
}

function updateOrthoFrustum(aspect = view.persp.aspect) {
  const ortho = view.ortho;
  const halfH = view.orthoHalf;
  ortho.left = -halfH * aspect;
  ortho.right = halfH * aspect;
  ortho.top = halfH;
  ortho.bottom = -halfH;
  ortho.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  if (view.controls) view.controls.update();
  renderFrame();
  if (store.building && performance.now() - view.lastPaint > 250) {
    view.lastPaint = performance.now();
    paintProgress();
  }
}

function renderFrame() {
  if (!view.renderer || !view.scene || !view.camera || !view.controls) return;
  const canvas = view.canvas;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;

  view.renderer.setScissorTest(false);
  view.renderer.setViewport(0, 0, width, height);
  view.renderer.clear(true, true, false);
  view.renderer.render(view.scene, view.camera);

  // small orientation triad in the lower-left corner
  const size = 78;
  view.gizmoDir.subVectors(view.camera.position, view.controls.target);
  if (view.gizmoDir.lengthSq() < 1e-9) view.gizmoDir.set(1, -1, 1);
  view.gizmoDir.normalize();
  view.gizmoCamera.quaternion.copy(view.camera.quaternion);
  view.gizmoCamera.position.copy(view.gizmoDir).multiplyScalar(3.2);
  view.renderer.setViewport(12, 12, size, size);
  view.renderer.setScissor(12, 12, size, size);
  view.renderer.setScissorTest(true);
  view.renderer.clearDepth();
  view.renderer.render(view.gizmoScene, view.gizmoCamera);
  view.renderer.setScissorTest(false);
}

// --------------------------------------------------------------------- camera

function sceneBox() {
  const box = new THREE.Box3();
  for (const partView of store.revisionGroups.values()) {
    if (partView.box && !partView.box.isEmpty()) box.union(partView.box);
  }
  if (box.isEmpty()) {
    box.set(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50));
  }
  return box;
}

function applyView(name) {
  const preset = VIEWS[name];
  if (!preset) return;
  const target = view.controls.target.clone();
  const distance = Math.max(view.camera.position.distanceTo(target), 1e-3);
  const dir = new THREE.Vector3(preset.eye[0], preset.eye[1], preset.eye[2]).normalize();
  view.camera.up.set(preset.up[0], preset.up[1], preset.up[2]);
  view.camera.position.copy(target).addScaledVector(dir, distance);
  view.camera.lookAt(target);
  view.controls.target.copy(target);
  view.controls.update();
}

function fitView(box = sceneBox()) {
  const target = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1);

  let dir = view.camera.position.clone().sub(view.controls.target);
  if (dir.lengthSq() < 1e-9) dir.set(1, -1, 1);
  dir.normalize();

  let up = view.camera.up.clone();
  if (Math.abs(up.dot(dir)) > 0.999) up.set(0, 1, 0);
  if (Math.abs(up.dot(dir)) > 0.999) up.set(1, 0, 0);

  if (view.mode === 'persp') {
    const camera = view.persp;
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * Math.max(camera.aspect, 1e-3));
    const distance = (radius / Math.sin(Math.min(vFov, hFov) / 2)) * 1.08;
    camera.up.copy(up);
    camera.position.copy(target).addScaledVector(dir, distance);
    camera.lookAt(target);
  } else {
    const camera = view.ortho;
    const aspect = view.persp.aspect;
    view.orthoHalf = (radius * 1.08) / Math.min(Math.max(aspect, 1e-3), 1);
    updateOrthoFrustum(aspect);
    camera.up.copy(up);
    camera.position.copy(target).addScaledVector(dir, Math.max(radius * 4, 10));
    camera.lookAt(target);
  }

  view.controls.target.copy(target);
  view.controls.update();
}

function focusPoint(at) {
  if (!Array.isArray(at) || at.length < 3) return;
  const point = new THREE.Vector3(Number(at[0]), Number(at[1]), Number(at[2]));
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || !Number.isFinite(point.z)) return;
  const dir = view.camera.position.clone().sub(view.controls.target);
  if (dir.lengthSq() < 1e-9) dir.set(1, -1, 1);
  const distance = Math.max(dir.length() * 0.6, view.sceneRadius * 0.5, 1);
  dir.normalize();
  view.controls.target.copy(point);
  view.camera.position.copy(point).addScaledVector(dir, distance);
  view.camera.lookAt(point);
  if (view.mode === 'ortho') {
    view.orthoHalf = Math.max(view.orthoHalf * 0.7, 1e-3);
    updateOrthoFrustum();
  }
  view.controls.update();
}

function toggleCamera() {
  const mode = view.mode === 'persp' ? 'ortho' : 'persp';
  const target = view.controls.target.clone();
  const dir = view.camera.position.clone().sub(target);
  const distance = Math.max(dir.length(), 1e-3);
  if (dir.lengthSq() < 1e-12) dir.set(1, -1, 1);
  dir.normalize();

  const camera = mode === 'ortho' ? view.ortho : view.persp;
  camera.position.copy(target).addScaledVector(dir, distance);
  camera.up.copy(view.camera.up);
  camera.lookAt(target);
  if (mode === 'ortho') {
    view.orthoHalf = Math.max(distance * Math.tan(THREE.MathUtils.degToRad(view.persp.fov) / 2), 1e-3);
    updateOrthoFrustum();
  }

  view.camera = camera;
  view.mode = mode;
  attachControls();
  view.controls.target.copy(target);
  view.controls.update();
  setText(byId('btn-camera'), mode === 'persp' ? 'Perspective' : 'Orthographic');
}

// --------------------------------------------------------------------- materials / views

function trackMaterial(material) {
  view.materials.add(material);
  if (store.section.on && view.backend !== 'webgpu') material.clippingPlanes = [view.clipPlane];
  return material;
}

function createView(parsed, options) {
  const group = new THREE.Group();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(parsed.positions, 3));
  if (parsed.normals.length && parsed.normals.length === parsed.positions.length) {
    geometry.setAttribute('normal', new THREE.BufferAttribute(parsed.normals, 3));
  }
  if (parsed.indices.length) geometry.setIndex(new THREE.BufferAttribute(parsed.indices, 1));
  if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();

  const vertexColors = Boolean(options.vertexColors) && parsed.colors.length >= parsed.positions.length;
  if (vertexColors) geometry.setAttribute('color', new THREE.BufferAttribute(parsed.colors, 3));
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();

  const opacity = options.opacity == null ? 1 : options.opacity;
  const material = new THREE.MeshStandardMaterial({
    color: vertexColors ? 0xffffff : new THREE.Color(options.color || PALETTE[0]),
    vertexColors,
    metalness: 0.04,
    roughness: 0.62,
    side: THREE.DoubleSide,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 1,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  });
  trackMaterial(material);

  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData.partName = options.name || null;
  mesh.userData.faceIds = options.pickable ? parsed.faceIds : null;
  group.add(mesh);

  const segments = edgeSegments(parsed);
  let edges = null;
  if (segments.length) {
    const edgeGeometry = new THREE.BufferGeometry();
    edgeGeometry.setAttribute('position', new THREE.BufferAttribute(segments, 3));
    const edgeMaterial = new THREE.LineBasicMaterial({
      color: EDGE_COLOR,
      transparent: true,
      opacity: opacity < 1 ? 0.4 : 0.85,
    });
    trackMaterial(edgeMaterial);
    edges = new THREE.LineSegments(edgeGeometry, edgeMaterial);
    edges.renderOrder = 2;
    group.add(edges);
  }

  return {
    group,
    mesh,
    edges,
    geometry,
    parsed,
    box: geometry.boundingBox && !geometry.boundingBox.isEmpty() ? geometry.boundingBox.clone() : null,
    triangles: parsed.indices.length / 3,
    frameIndex: options.frameIndex == null ? null : options.frameIndex,
  };
}

function disposeObject(object) {
  object.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
    if (node.material) {
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) {
        view.materials.delete(material);
        material.dispose();
      }
    }
  });
}

function disposeView(partView) {
  if (!partView) return;
  partView.group.removeFromParent();
  disposeObject(partView.group);
}

// --------------------------------------------------------------------- geometry load

async function loadRevisionGeometry(doc) {
  const revision = doc.revision;
  const names = Object.keys(doc.parts || {});
  if (revision == null) {
    clearRevisionGeometry();
    return;
  }
  const key = names.slice().sort().join('|');
  if (store.loadedRevision === revision && store.loadedPartsKey === key && !store.geometryError) return;

  const seq = ++store.geometrySeq;
  store.loadedRevision = revision;
  store.loadedPartsKey = key;
  store.geometryError = null;

  for (const [name, partView] of [...store.revisionGroups]) {
    if (!names.includes(name)) {
      disposeView(partView);
      store.revisionGroups.delete(name);
    }
  }
  updateEmptyState();

  const results = await Promise.allSettled(names.map(async (name, index) => {
    const response = await fetch(
      apiPath(`/api/mesh?part=${encodeURIComponent(name)}&rev=${encodeURIComponent(revision)}`),
      { cache: 'no-store' },
    );
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        message = (body && body.error && body.error.message) || message;
      } catch { /* not a JSON error body */ }
      throw new Error(`${name}: ${message}`);
    }
    const parsed = parseMesh(await response.arrayBuffer());
    return { name, index, parsed };
  }));

  if (seq !== store.geometrySeq) return;

  const failed = [];
  results.forEach((result) => {
    if (result.status === 'fulfilled') {
      const { name, index, parsed } = result.value;
      const info = (doc.parts && doc.parts[name]) || {};
      const previous = store.revisionGroups.get(name);
      const partView = createView(parsed, {
        name,
        color: colourFor(info, index),
        opacity: info.reference ? 0.25 : 1,
        pickable: true,
        frameIndex: null,
      });
      view.clipRoot.add(partView.group);
      if (previous) disposeView(previous);
      store.revisionGroups.set(name, partView);
    } else {
      failed.push(result.reason && result.reason.message ? result.reason.message : String(result.reason));
    }
  });

  if (failed.length) {
    store.geometryError = failed.join('; ');
    pushNote('error', `mesh unavailable — ${store.geometryError}`);
  }
  applyVisibility();
  refreshBounds();
  refreshPrintOverlay();
  refreshBedPreviewAfterGeometry();
  renderBuildDiagnostics();
  updateStatus();
  updateEmptyState();
}

function clearRevisionGeometry() {
  store.geometrySeq += 1;
  clearPrintOverlay();
  clearBedPlane();
  store.bedPreview.active = false;
  store.bedPreview.prev = null;
  clearGround();
  syncPrintToggles();
  for (const partView of store.revisionGroups.values()) disposeView(partView);
  store.revisionGroups.clear();
  clearFrameViews();
  store.frames.clear();
  store.bounds = null;
  clearHighlight('sel');
  clearHighlight('hover');
  store.selection = null;
  setChatSelection(null);
  renderSelection();
  updateStatus();
  updateEmptyState();
}

function applyVisibility() {
  const feaHidden = store.fea && store.fea.part ? store.fea.part : null;
  for (const [name, partView] of store.revisionGroups) {
    const on = store.visibility.get(name) !== false;
    partView.group.visible = on && name !== feaHidden && !store.frameGroups.has(name);
  }
  for (const [name, partView] of store.frameGroups) {
    partView.group.visible = store.visibility.get(name) !== false;
  }
  if (store.fea) store.fea.view.group.visible = true;
  updateHighlights();
}

function clearFrameViews() {
  store.frameSeq += 1;
  for (const partView of store.frameGroups.values()) disposeView(partView);
  store.frameGroups.clear();
  applyVisibility();
  updateStatus();
}

function applyFrameView(name, parsed, partInfo, index) {
  const previous = store.frameGroups.get(name);
  if (previous && previous.frameIndex === index) return;
  const partView = createView(parsed, {
    name,
    color: colourFor(partInfo, index),
    opacity: partInfo && partInfo.reference ? 0.25 : 1,
    pickable: false,
    frameIndex: index,
  });
  view.clipRoot.add(partView.group);
  if (previous) disposeView(previous);
  store.frameGroups.set(name, partView);
}

// --------------------------------------------------------------------- bounds / ground

function refreshBounds() {
  const box = sceneBox();
  store.bounds = box;
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
  view.sceneRadius = radius;

  view.persp.near = Math.max(radius / 5000, 0.005);
  view.persp.far = Math.max(radius * 500, 200);
  view.persp.updateProjectionMatrix();
  view.ortho.near = 0.01;
  view.ortho.far = Math.max(radius * 400, 500);
  view.ortho.updateProjectionMatrix();

  if (store.revisionGroups.size) {
    rebuildGround(box);
  } else {
    clearGround();
    store.didFirstFit = false;
  }
  updateSectionBounds(box);
  if (!store.didFirstFit && store.revisionGroups.size) {
    store.didFirstFit = true;
    fitView(box);
  }
  updateEmptyState();
}

function clearGround() {
  if (!view.ground) return;
  view.scene.remove(view.ground);
  disposeObject(view.ground);
  view.ground = null;
}

function rebuildGround(box) {
  clearGround();
  const group = new THREE.Group();
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 10);
  const step = niceNumber(maxDim / 6);
  const divisions = 16;
  const gridSize = step * divisions;
  const center = box.getCenter(new THREE.Vector3());
  const groundZ = Math.min(0, box.min.z);

  const grid = new THREE.GridHelper(gridSize, divisions, 0x384453, 0x242d37);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(center.x, center.y, groundZ);
  grid.material.transparent = true;
  grid.material.opacity = 0.75;
  grid.material.depthWrite = false;
  group.add(grid);

  const pad = step * 0.3;
  const z = groundZ + 0.01;
  const pts = [
    new THREE.Vector3(box.min.x - pad, box.min.y - pad, z),
    new THREE.Vector3(box.max.x + pad, box.min.y - pad, z),
    new THREE.Vector3(box.max.x + pad, box.max.y + pad, z),
    new THREE.Vector3(box.min.x - pad, box.max.y + pad, z),
    new THREE.Vector3(box.min.x - pad, box.min.y - pad, z),
  ];
  const outline = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x4a4a4a, transparent: true, opacity: 0.85 }),
  );
  group.add(outline);

  const axes = new THREE.AxesHelper(Math.max(step * 1.5, maxDim * 0.05));
  axes.position.set(0, 0, z);
  axes.material.transparent = true;
  axes.material.opacity = 0.7;
  group.add(axes);

  view.ground = group;
  view.scene.add(group);
}

// --------------------------------------------------------------------- section planes

function updateClipPlane() {
  const section = store.section;
  const axis = new THREE.Vector3(
    section.axis === 'x' ? 1 : 0,
    section.axis === 'y' ? 1 : 0,
    section.axis === 'z' ? 1 : 0,
  );
  const dir = section.flip ? 1 : -1; // clip-side normal; false keeps everything below the plane
  view.clipPlane.normal.copy(axis).multiplyScalar(dir);
  view.clipPlane.constant = -dir * section.value;

  if (view.backend === 'webgpu' && view.clipRoot) {
    view.clipRoot.clippingPlanes = section.on ? [view.clipPlane] : [];
    view.clipRoot.enabled = section.on;
    return;
  }

  for (const material of view.materials) {
    if (section.on) {
      if (!material.clippingPlanes || material.clippingPlanes.length !== 1) {
        material.clippingPlanes = [view.clipPlane];
        material.needsUpdate = true;
      }
    } else if (material.clippingPlanes) {
      material.clippingPlanes = null;
      material.needsUpdate = true;
    }
  }
}

function renderSectionValue() {
  setText(byId('section-value'), `${fmt(store.section.value, 2)} mm`);
}

function updateSectionBounds(box) {
  const section = store.section;
  section.min = box.min[section.axis];
  section.max = box.max[section.axis];
  if (!section.initialised || !(section.value >= section.min && section.value <= section.max)) {
    section.value = section.max;
    section.initialised = true;
  }
  const slider = byId('section-slider');
  slider.min = String(section.min);
  slider.max = String(section.max);
  slider.step = String(Math.max((section.max - section.min) / 500, 1e-4));
  slider.value = String(section.value);
  slider.disabled = !section.on;
  renderSectionValue();
  updateClipPlane();
}

function setSectionAxis(axis) {
  if (!['x', 'y', 'z'].includes(axis)) return;
  store.section.axis = axis;
  store.section.initialised = false;
  for (const button of document.querySelectorAll('#right [data-axis]')) {
    button.classList.toggle('on', button.dataset.axis === axis);
  }
  if (store.bounds) updateSectionBounds(store.bounds);
}

// --------------------------------------------------------------------- picking

function pointerNdc(clientX, clientY) {
  const rect = view.canvas.getBoundingClientRect();
  return new THREE.Vector2(
    ((clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1,
    -((clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1,
  );
}

function raycastAt(clientX, clientY) {
  view.raycaster.setFromCamera(pointerNdc(clientX, clientY), view.camera);
  const targets = [];
  for (const partView of store.revisionGroups.values()) {
    if (!partView.group.visible) continue;
    if (!partView.mesh.userData.faceIds) continue;
    targets.push(partView.mesh);
  }
  if (!targets.length) return null;
  const hits = view.raycaster.intersectObjects(targets, false);
  if (!hits.length) return null;
  const hit = hits[0];
  const faceIds = hit.object.userData.faceIds;
  const faceId = hit.faceIndex != null && faceIds && hit.faceIndex < faceIds.length
    ? faceIds[hit.faceIndex]
    : null;
  return {
    part: hit.object.userData.partName,
    faceId,
    point: hit.point.clone(),
  };
}

function faceTable(part, revision) {
  const key = `${part}@${revision}`;
  let pending = store.faceTables.get(key);
  if (!pending) {
    pending = fetch(
      apiPath(`/api/faces?part=${encodeURIComponent(part)}&rev=${encodeURIComponent(revision)}`),
      { cache: 'no-store' },
    )
      .then((response) => {
        if (!response.ok) throw new Error(`face table HTTP ${response.status}`);
        return response.json();
      })
      .then((body) => {
        const table = new Map();
        for (const face of body.faces || []) table.set(face.id, face);
        return table;
      });
    pending.catch(() => store.faceTables.delete(key));
    store.faceTables.set(key, pending);
  }
  return pending;
}

function selectFace(hit) {
  if (!hit) {
    clearSelection();
    return;
  }
  store.selection = {
    part: hit.part,
    faceId: hit.faceId,
    point: hit.point ? hit.point.clone() : null,
    info: null,
    error: '',
  };
  store.activePart = hit.part;
  setChatSelection({
    revision: store.revision,
    part: hit.part,
    kind: hit.faceId == null ? 'part' : 'face',
    ...(hit.faceId == null ? {} : { face: hit.faceId }),
  });
  renderParts();
  renderPartInfo();
  renderFeatureLedger();
  renderSelection();
  syncPrintContext();
  renderPrintPanel();
  updateHighlights();
  if (hit.faceId != null) loadFaceInfo(hit.part, hit.faceId, ++store.faceSeq);
  if (store.bedPreview.active) enableBedPreview(previewTarget());
}

function clearSelection() {
  const hadPreview = store.bedPreview.active;
  store.selection = null;
  setChatSelection(null);
  store.faceSeq += 1;
  if (hadPreview) disableBedPreview(true);
  renderSelection();
  renderFeatureLedger();
  updateHighlights();
}

async function loadFaceInfo(part, faceId, seq) {
  try {
    const table = await faceTable(part, store.revision);
    const info = table.get(faceId) || null;
    const selection = store.selection;
    if (seq !== store.faceSeq || !selection || selection.part !== part || selection.faceId !== faceId) return;
    selection.info = info;
    const target = store.chatSelection;
    if (target && target.kind === 'face' && target.part === part && target.face === faceId) {
      const ref = info && Array.isArray(info.refs) ? info.refs.find(Boolean) : null;
      setChatSelection({ ...target, ...(ref ? { ref } : {}) });
    }
    if (!info) selection.error = 'face is not in the face table';
    const bp = store.bedPreview;
    if (bp.active && bp.part === part && bp.faceId === faceId) {
      const refs = info && Array.isArray(info.refs) ? info.refs.filter(Boolean) : [];
      bp.ref = refs.length ? refs[0] : null;
    }
    renderSelection();
  } catch (error) {
    const selection = store.selection;
    if (seq === store.faceSeq && selection && selection.part === part && selection.faceId === faceId) {
      selection.error = String(error.message || error);
      renderSelection();
    }
  }
}

function setHover(hit) {
  const previous = store.hover;
  if (!hit && !previous) return;
  if (hit && previous && hit.part === previous.part && hit.faceId === previous.faceId) return;
  store.hover = hit
    ? { part: hit.part, faceId: hit.faceId, point: hit.point, info: null, error: '' }
    : null;
  const seq = ++store.hoverSeq;
  if (hit && hit.faceId != null) loadFaceHover(hit.part, hit.faceId, seq);
  renderSelection();
  updateHighlights();
}

async function loadFaceHover(part, faceId, seq) {
  try {
    const table = await faceTable(part, store.revision);
    const info = table.get(faceId) || null;
    const hover = store.hover;
    if (seq !== store.hoverSeq || !hover || hover.part !== part || hover.faceId !== faceId) return;
    hover.info = info;
    renderSelection();
  } catch { /* a hover miss is not worth a banner */ }
}

function faceHighlight(parsed, face, opacity) {
  const matches = face instanceof Set ? (id) => face.has(id) : (id) => face === id;
  if (!parsed.faceIds.length || !parsed.indices.length) return null;
  let count = 0;
  for (let t = 0; t < parsed.faceIds.length; t += 1) {
    if (matches(parsed.faceIds[t])) count += 1;
  }
  if (!count) return null;
  const vertices = new Float32Array(count * 9);
  let offset = 0;
  for (let t = 0; t < parsed.faceIds.length; t += 1) {
    if (!matches(parsed.faceIds[t])) continue;
    for (let k = 0; k < 3; k += 1) {
      const index = parsed.indices[t * 3 + k] * 3;
      vertices[offset] = parsed.positions[index];
      vertices[offset + 1] = parsed.positions[index + 1];
      vertices[offset + 2] = parsed.positions[index + 2];
      offset += 3;
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  const material = new THREE.MeshBasicMaterial({
    color: 0xa6dec9,
    transparent: true,
    opacity,
    depthWrite: false,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  trackMaterial(material);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = 5;
  return { mesh, material };
}

function makeHighlight(target, opacity) {
  const partView = store.revisionGroups.get(target.part);
  if (!partView?.group.visible) return null;
  const highlight = faceHighlight(partView.parsed, target.faceId, opacity);
  if (!highlight) return null;
  view.clipRoot.add(highlight.mesh);
  return highlight;
}

function clearHighlight(which) {
  const key = which === 'hover' ? 'hoverHighlight' : 'selHighlight';
  const highlight = store[key];
  if (!highlight) return;
  highlight.mesh.removeFromParent();
  highlight.mesh.geometry.dispose();
  view.materials.delete(highlight.material);
  highlight.material.dispose();
  store[key] = null;
}

function updateHighlights() {
  clearHighlight('hover');
  clearHighlight('sel');
  const selection = store.selection;
  const hover = store.hover;
  const same = selection && hover && selection.part === hover.part && selection.faceId === hover.faceId;
  if (hover && hover.faceId != null && !same) store.hoverHighlight = makeHighlight(hover, 0.16);
  if (selection && selection.faceId != null) store.selHighlight = makeHighlight(selection, 0.48);
  else if (store.chatSelection?.kind === 'feature' && store.featureFaceIds?.size) {
    store.selHighlight = makeHighlight({
      part: store.chatSelection.part, faceId: store.featureFaceIds,
    }, 0.58);
  }
}

// --------------------------------------------------------------------- panels

function activePartName() {
  const parts = (store.doc && store.doc.parts) || {};
  if (store.activePart && parts[store.activePart]) return store.activePart;
  return Object.keys(parts)[0] || null;
}

function kvBlock(rows) {
  const box = el('div', 'kv');
  for (const [key, value, className] of rows) {
    box.appendChild(el('span', null, key));
    box.appendChild(el('span', ['cad-readout', className].filter(Boolean).join(' '), value == null ? '-' : value));
  }
  return box;
}

function updateChrome() {
  const doc = store.doc;
  setText(byId('project-name'), (doc && (doc.project_title || doc.project)) || 'no project');
  byId('projects-link').classList.toggle('hidden', !doc?.projects_url);
  setText(byId('project-entry'), (doc && doc.entry) || '');

  const info = byId('project-info');
  clearNode(info);
  if (doc) {
    info.appendChild(el('span', null, 'root'));
    const root = el('span', null, doc.root || '-');
    root.title = doc.root || '';
    info.appendChild(root);
    info.appendChild(el('span', null, 'head'));
    info.appendChild(el('span', null, doc.head != null ? `r${doc.head}` : '-'));
    info.appendChild(el('span', null, 'version'));
    info.appendChild(el('span', null, doc.version || '-'));
    info.appendChild(el('span', null, 'parts'));
    info.appendChild(el('span', null, String(Object.keys(doc.parts || {}).length)));
  }

  const stdout = (doc && doc.stdout) || '';
  byId('stdout-box').classList.toggle('hidden', !stdout);
  setText(byId('stdout'), stdout);
}

function renderParts() {
  const host = byId('parts-list');
  clearNode(host);
  const parts = (store.doc && store.doc.parts) || {};
  const names = Object.keys(parts);
  setText(byId('part-count'), names.length ? String(names.length) : '');
  if (!names.length) {
    host.appendChild(el('div', 'note', 'no parts'));
    return;
  }
  names.forEach((name, index) => {
    const part = parts[name] || {};
    const summary = part.summary || {};
    const row = el('div', 'part-row');
    if (activePartName() === name) row.classList.add('active');

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = store.visibility.get(name) !== false;
    checkbox.title = 'visible';
    checkbox.addEventListener('change', () => {
      store.visibility.set(name, checkbox.checked);
      if (!checkbox.checked && store.chatSelection && store.chatSelection.part === name) clearSelection();
      applyVisibility();
      renderPrintPanel();
      updateStatus();
    });

    const swatch = el('span', 'swatch');
    swatch.style.background = colourFor(part, index);

    const button = el('button', 'part-name', name);
    button.type = 'button';
    button.title = part.reference ? `${name} (reference)` : name;
    button.addEventListener('click', () => setActivePart(name));

    row.append(checkbox, swatch, button);
    if (part.reference) row.appendChild(el('span', 'tag', 'ref'));
    if (summary.volume != null) {
      row.appendChild(el('span', 'part-meta', `${fmt(summary.volume, 0)} mm³`));
    } else if (summary.solids != null) {
      row.appendChild(el('span', 'part-meta', `${summary.solids} solids`));
    }
    host.appendChild(row);
  });
}

function setActivePart(name) {
  clearSelection();
  store.activePart = name;
  setChatSelection({ revision: store.revision, part: name, kind: 'part' });
  renderSelection();
  renderParts();
  renderPartInfo();
  renderFeatureLedger();
  syncPrintContext();
  renderPrintPanel();
  if (timelineFrames(name).length && store.timelinePart !== name) {
    if (store.timelinePlaying) stopPlay();
    store.timelinePart = name;
    store.timelineSig = '';
    store.timelineStep = Math.max(0, timelineSteps().length - 1);
    clearFrameViews();
    renderTimeline();
  }
}

function renderPartInfo() {
  const host = byId('part-info');
  const detailsOpen = host.querySelector('details')?.open ?? false;
  clearNode(host);
  const name = activePartName();
  const parts = (store.doc && store.doc.parts) || {};
  const part = name ? parts[name] : null;
  if (!name || !part) {
    host.appendChild(el('div', 'note', 'no part'));
    return;
  }
  const summary = part.summary || {};
  const partView = store.frameGroups.get(name) || store.revisionGroups.get(name);
  host.appendChild(el('div', 'fea-title', name));
  const rows = [
    ['Size', summary.bbox && Array.isArray(summary.bbox.size)
      ? `${summary.bbox.size.map((v) => fmt(v, 1)).join(' × ')} mm`
      : '-'],
    ['Volume', summary.volume != null ? `${fmt(summary.volume, 0)} mm³` : '-'],
    ['Area', summary.area != null ? `${fmt(summary.area, 1)} mm²` : '-'],
    ['Solids', summary.solids != null ? fmtInt(summary.solids) : '-'],
    ['Valid', summary.valid == null ? '-' : summary.valid ? 'Yes' : 'No',
      summary.valid == null ? '' : summary.valid ? 'ok' : 'bad'],
  ];
  if (part.reference) rows.push(['Reference', 'Yes']);
  host.appendChild(kvBlock(rows));

  const details = el('details', 'part-details');
  details.open = detailsOpen;
  details.append(el('summary', null, 'Mesh and material'), kvBlock([
    ['Faces', summary.faces != null ? fmtInt(summary.faces) : '-'],
    ['Holes', summary.holes != null ? fmtInt(summary.holes) : '-'],
    ['Triangles', partView ? fmtInt(partView.triangles) : '-'],
    ['Mass', summary.mass_g != null ? `${fmt(summary.mass_g, 1)} g` : '-'],
    ['Material', summary.material || '-'],
  ]));
  host.appendChild(details);
}

function featureErrorText(feature) {
  if (!feature || !feature.error) return feature && feature.status ? String(feature.status) : '';
  if (typeof feature.error === 'string') return feature.error;
  return [feature.error.code, feature.error.message].filter(Boolean).join(': ');
}

function renderFeatureLedger() {
  const host = byId('feature-ledger');
  clearNode(host);
  const name = activePartName();
  const parts = (store.doc && store.doc.parts) || {};
  const part = name ? parts[name] : null;
  const features = part && Array.isArray(part.features) ? part.features : [];
  setText(byId('feature-count'), features.length ? String(features.length) : '');
  if (!name) {
    host.appendChild(el('div', 'note', 'no part'));
    return;
  }
  if (!features.length) {
    host.appendChild(el('div', 'note', 'no features'));
    return;
  }
  const head = el('div', 'ledger-head');
  head.append(el('span', null, 'Feature'), el('span', null, 'Type'),
    el('span', 'led-ms', 'ms'), el('span', 'led-vol', 'mm³'));
  host.appendChild(head);
  features.forEach((feature, index) => {
    const failed = Boolean(feature.status && feature.status !== 'ok');
    const row = el('button', `ledger-row${failed ? ' bad' : ''}`);
    row.type = 'button';
    row.dataset.featureId = feature.id || '';
    const selected = store.chatSelection;
    row.classList.toggle('selected', Boolean(selected && selected.kind === 'feature'
      && selected.part === name && selected.feature === feature.id));
    row.setAttribute('aria-pressed', String(row.classList.contains('selected')));
    const volume = feature.metrics && feature.metrics.volume != null
      ? fmt(feature.metrics.volume, 0)
      : '-';
    row.append(
      el('span', 'led-id', feature.id ? feature.id.replaceAll('_', ' ') : `#${index}`),
      el('span', 'led-type', feature.type || ''),
      el('span', 'led-ms', feature.ms != null ? fmt(feature.ms, 1) : '-'),
      el('span', 'led-vol', volume),
    );
    row.title = failed
      ? (featureErrorText(feature) || 'failed')
      : `${feature.id || ''} ${feature.type || ''}${feature.ms != null ? ` — ${fmt(feature.ms, 1)} ms` : ''}${volume !== '-' ? `, ${volume} mm³` : ''}`;
    row.addEventListener('click', () => {
      selectFeature(name, feature.id);
      // Rendering the selection refreshes the ledger. Keep keyboard focus on the row.
      host.children[index + 1]?.focus({ preventScroll: true });
    });
    host.appendChild(row);
  });
}

async function selectFeature(part, feature) {
  if (store.timelinePlaying) stopPlay();
  clearSelection();
  const target = feature
    ? { revision: store.revision, part, kind: 'feature', feature: String(feature) }
    : { revision: store.revision, part, kind: 'part' };
  setChatSelection(target);
  const finalStep = Math.max(0, timelineSteps().length - 1);
  if (store.timelineStep !== finalStep) setTimelineStep(finalStep);
  renderSelection();
  renderFeatureLedger();
  if (target.kind !== 'feature') return;

  try {
    const faces = await faceTable(part, target.revision);
    if (store.chatSelection !== target || store.revision !== target.revision) return;
    const prefix = `${target.feature}.`;
    store.featureFaceIds = new Set([...faces.values()]
      .filter((face) => Array.isArray(face.refs) && face.refs.some((ref) => ref.startsWith(prefix)))
      .map((face) => face.id));
    updateHighlights();
  } catch {
    // Features without indexed faces remain available for chat and build replay.
  }
}

function jumpToFeature(featureId) {
  const name = activePartName();
  if (!name || featureId == null) return false;
  const frames = timelineFrames(name);
  if (!frames.length) return false;
  const frame = frames.filter((f) => f.id === featureId).pop();
  if (!frame) return false;
  if (store.timelinePart !== name) {
    store.timelinePart = name;
    store.timelineSig = '';
    renderTimeline();
  }
  const steps = timelineSteps();
  const position = steps.findIndex((step) => step.kind === 'frame' && step.index === frame.index);
  if (position < 0) return false;
  if (store.timelinePlaying) stopPlay();
  setTimelineStep(position);
  return true;
}

function queueParam(name, value) {
  if (isBrowsingHistory()) {
    showToast(`viewing r${store.viewRevision} — return to the current revision to change a parameter`);
    return;
  }
  store.paramPending[name] = value;
  if (store.paramTimer) clearTimeout(store.paramTimer);
  store.paramTimer = setTimeout(commitParams, 250);
}

async function commitParams() {
  store.paramTimer = 0;
  const values = store.paramPending;
  store.paramPending = {};
  if (!Object.keys(values).length) return;
  try {
    const response = await fetch(apiPath('/api/param'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values, viewing: store.doc?.revision }),
    });
    const body = await response.json();
    if (!response.ok || !body.ok) {
      throw new Error((body && body.error && body.error.message) || `parameter update HTTP ${response.status}`);
    }
    if (store.doc) store.doc.overrides = body.overrides || store.doc.overrides;
    postBoardState();
  } catch (error) {
    pushNote('error', `parameter update failed: ${error.message || error}`);
  }
}

function diagRow({ code, message, hint, location, kind, title }) {
  const row = el('div', `diag-row ${kind || ''}`.trim());
  row.appendChild(el('span', 'diag-code', code || 'note'));
  const msg = el('span', 'diag-msg', message || code || '');
  if (location) msg.appendChild(el('span', 'diag-loc', location));
  if (hint) msg.appendChild(el('span', 'diag-hint', hint));
  row.appendChild(msg);
  row.title = [code, message, title || '', location, hint].filter(Boolean).join(' — ');
  return row;
}

function buildDiagnosticItems() {
  const doc = store.doc;
  const items = [];
  const build = (doc && doc.last_build) || {};
  let error = doc && doc.error;
  if (!error && build.status === 'failed' && build.error) error = build.error;
  if (error) {
    items.push({
      code: error.code || 'BUILD_FAILED',
      message: error.message || 'build failed',
      hint: error.hint || '',
      location: locationText(error),
      kind: 'bad',
    });
  }
  for (const warning of (doc && doc.warnings) || []) {
    const where = warning.part && warning.feature
      ? `${warning.part} · ${warning.feature}`
      : (warning.part || warning.feature || '');
    items.push({
      code: warning.code || 'warning',
      message: warning.message || '',
      hint: [where, warning.hint].filter(Boolean).join(' · '),
      kind: '',
    });
  }
  const parts = (doc && doc.parts) || {};
  for (const [name, part] of Object.entries(parts)) {
    const summary = (part && part.summary) || {};
    if (summary.valid === false) {
      items.push({ code: 'INVALID', message: `${name} is not a valid solid`, kind: 'bad' });
    }
  }
  if (store.geometryError) {
    items.push({ code: 'MESH', message: store.geometryError, kind: 'bad' });
  }
  return items;
}

function renderBuildDiagnostics() {
  const host = byId('build-diag-list');
  if (!host) return;
  const items = buildDiagnosticItems();
  clearNode(host);
  setText(byId('diag-build-count'), String(items.length));
  if (!items.length) {
    host.appendChild(el('div', 'diag-none', 'none'));
    return;
  }
  for (const item of items) host.appendChild(diagRow(item));
}

function renderPrintDiagnostics() {
  const host = byId('print-diag-list');
  if (!host) return;
  const data = printDataForCurrent();
  let issues = store.print.error ? 1 : 0;
  if (data) {
    const thin = data.thin || {};
    const overhang = data.overhang || {};
    issues += Number(!data.bed?.fits)
      + Number(Boolean(thin.count ?? thin.faces?.length))
      + Number(Boolean(overhang.count ?? overhang.faces?.length))
      + (data.warnings || []).filter(({ code }) => code !== 'OVERHANG').length;
  }
  const count = byId('diag-print-count');
  setText(count, issues ? `${issues} issue${issues === 1 ? '' : 's'}` : '');
  count.classList.toggle('has-issues', issues > 0);
  const status = store.print.loading ? 'Checking…'
    : issues ? '' : data ? 'No issues found' : 'No print data';
  setText(host, status);
}

function renderDiagnostics() {
  renderBuildDiagnostics();
  renderPrintDiagnostics();
}

function renderActivity() {
  const host = byId('activity');
  clearNode(host);
  if (!store.notes.length) {
    host.appendChild(el('div', 'note', 'no events yet'));
    return;
  }
  for (const note of store.notes.slice(0, 8)) {
    const row = el('div', `act-row ${note.level || ''}`);
    row.append(el('span', null, note.time), el('span', null, note.message));
    host.appendChild(row);
  }
}

// --------------------------------------------------------------------- selection panel

function renderSelection() {
  const { chatSelection: target, selection, hover } = store;
  const face = selection || hover;
  const secondary = selection && hover && (hover.part !== selection.part || hover.faceId !== selection.faceId);
  window.cadWorkspace.inspect({
    target, face, selected: Boolean(selection), historical: isBrowsingHistory(),
    replay: target?.kind === 'feature' && timelineFrames(target.part).some(frame => frame.id === target.feature),
    area: face?.info?.area != null ? `${fmt(face.info.area, 2)} mm²` : '-',
    centroid: Array.isArray(face?.info?.center) ? `${fmtPoint(face.info.center)} mm` : '-',
    hover: secondary ? `hover: ${hover.part || '-'} · ${hover.faceId != null ? 'face ' + hover.faceId : 'no face'}` : null,
  });
  renderPrintFaceAction();
  renderBedPreview();
}

// --------------------------------------------------------------------- stress

function stressSelect(id, label, choices, value, change) {
  const row = el('div', 'stress-control');
  const caption = el('label', null, label);
  caption.htmlFor = id;
  const select = el('select');
  select.id = id;
  select.disabled = store.feaRunning;
  for (const [key, title] of choices) {
    const option = el('option', null, title);
    option.value = key;
    select.appendChild(option);
  }
  select.value = value;
  select.addEventListener('change', () => {
    const selected = select.value;
    change(selected);
    byId(id)?.focus();
  });
  row.append(caption, select);
  return row;
}

function studyLoadLabel(load) {
  const where = load.faces ? ` · ${load.faces}` : '';
  const direction = Array.isArray(load.direction) ? ` · [${load.direction.map((n) => fmt(n, 2)).join(', ')}]` : '';
  const axis = Array.isArray(load.axis) ? ` · axis [${load.axis.map((n) => fmt(n, 2)).join(', ')}]` : '';
  switch (load.type) {
    case 'fixed': return `Fixed ${(load.directions || 'xyz').toUpperCase()}${where}`;
    case 'force': return `Force ${fmt(load.newtons, 2)} N${direction}${where}`;
    case 'bearing': return `Bearing ${fmt(load.newtons, 2)} N${direction}${where}`;
    case 'torque': return `Torque ${fmt(load.newton_metres, 2)} N·m${axis}${where}`;
    case 'pressure': return `Pressure ${fmt(load.mpa, 3)} MPa${where}`;
    case 'gravity': return `Gravity ${fmt(load.g, 2)} g${direction}`;
    default: return `${load.type}${where}`;
  }
}

function draftStressStudy() {
  const part = store.chatSelection?.part || store.activePart;
  const subject = part ? ` for the "${part}" part` : '';
  const reference = store.chatSelection?.kind === 'face' ? ' Use the selected face if it matches the setup below.' : '';
  const text = `Add a stress study${subject} without changing the geometry.${reference}\n\nMaterial: [material]\nSupports: [which faces are fixed and in which directions]\nLoad: [force in N or pressure in MPa, face and direction]\nTarget safety factor: [target]`;
  draftInChat(text);
}

function draftInChat(text) {
  store.chat.pendingChatDraft = text;
  setChatOpen(true, { focus: true });
  postChatDraft();
}

function renderStress() {
  const host = byId('stress');
  clearNode(host);
  const doc = store.doc;
  if (!doc) {
    host.appendChild(el('p', 'note', 'Connecting to the project…'));
    return;
  }
  const caps = doc.capabilities || {};
  if (!caps.fea) {
    host.append(
      el('p', 'stress-message', 'Stress analysis is unavailable in this environment.'),
      el('p', 'note', caps.fea_unavailable || 'The finite element solver is missing.'),
    );
    if (caps.fea_install_hint && !WORKSPACE_PATH) host.appendChild(el('p', 'note', caps.fea_install_hint));
    return;
  }

  const studies = Array.isArray(doc.studies) ? doc.studies : [];
  if (!studies.length) {
    const empty = !Object.keys(doc.parts || {}).length;
    const history = isBrowsingHistory();
    host.append(
      el('p', 'stress-message', empty ? 'No part to analyze' : 'No studies on this revision'),
      el('p', 'note', empty ? 'Build a solid part, then define its material, supports, and loads.'
        : 'Define the material, where the part is held, and what force or pressure it needs to withstand.'),
    );
    const setup = el('button', 'wide', history ? 'Return to current revision' : empty ? 'Open Design chat' : 'Draft a study in chat');
    setup.type = 'button';
    setup.addEventListener('click', history ? returnToCurrentRevision
      : empty ? () => { setChatOpen(true, { focus: true }); postSelection(true); } : draftStressStudy);
    host.appendChild(setup);
    return;
  }

  const study = studies.find(({ name }) => name === store.study) || studies[0];
  store.study = study.name;
  host.appendChild(stressSelect('fea-study', 'Study', studies.map(({ name }) => [name, name]), study.name, (value) => {
    store.study = value;
    store.feaError = '';
    clearFea();
    updateStatus();
    renderStress();
  }));
  const setup = el('div', 'stress-setup');
  const material = partMaterialName(doc.parts?.[study.part]);
  setup.appendChild(el('p', 'stress-part', [study.part, material, `r${doc.revision}`].filter(Boolean).join(' · ')));
  if (study.description) setup.appendChild(el('p', 'note', study.description));
  const loads = el('ul', 'stress-loads');
  for (const load of study.loads || []) {
    loads.appendChild(el('li', null, studyLoadLabel(load)));
  }
  if (loads.childElementCount) setup.appendChild(loads);
  if (study.target != null) setup.appendChild(el('p', 'note', `Target safety factor: ${fmt(study.target, 2)}×`));
  host.appendChild(setup);
  host.appendChild(stressSelect('fea-quality', 'Mesh quality', [
    ['draft', 'Draft · quick check'], ['normal', 'Normal'], ['fine', 'Fine · slower'],
  ], store.feaQuality, (value) => {
    store.feaQuality = value;
    store.feaError = '';
    clearFea();
    updateStatus();
    renderStress();
  }));

  const run = el('button', 'wide stress-run', store.feaRunning ? 'Analyzing…' : store.fea ? 'Run again' : 'Run analysis');
  run.type = 'button';
  run.disabled = store.feaRunning || store.building || doc.revision == null;
  run.addEventListener('click', runFea);
  host.appendChild(run);

  const status = el('div', 'fea-status');
  status.setAttribute('role', 'status');
  if (store.feaRunning) {
    const spinner = el('span', 'spinner');
    spinner.setAttribute('aria-hidden', 'true');
    status.append(spinner, el('span', null, 'Meshing and solving. Fine meshes may take longer.'));
  } else if (store.fea) {
    status.textContent = 'Analysis complete';
  }
  host.appendChild(status);
  if (store.feaError) {
    const error = el('div', 'note bad', store.feaError);
    error.setAttribute('role', 'alert');
    host.appendChild(error);
  }
  if (store.fea) {
    host.appendChild(stressSelect('fea-field', 'Show on model', Object.entries(FIELD_LABELS), store.feaField, (value) => {
      store.feaField = value;
      void runFea();
    }));
    host.appendChild(renderFeaResult());
  }
}

function renderFeaResult() {
  const meta = (store.fea && store.fea.meta) || {};
  const legend = meta.legend || {};
  const report = meta.report || {};
  const wrap = el('div', 'fea-result');

  const metrics = el('div', 'stress-metrics');
  for (const [label, value, unit] of [
    ['Safety factor', report.safety_factor, '×'],
    ['Peak stress', report.max_von_mises_MPa, ' MPa'],
    ['Max displacement', report.max_displacement_mm, ' mm'],
  ]) {
    if (value == null) continue;
    const card = el('div', 'stress-metric');
    card.append(el('span', 'dim', label), el('strong', null, `${fmtNumber(value, 4)}${unit}`));
    metrics.appendChild(card);
  }
  wrap.appendChild(metrics);
  if (report.verdict) {
    wrap.appendChild(el('div', `verdict ${report.ok ? 'ok' : 'bad'}`, report.verdict));
  }
  if (report.material?.name) wrap.appendChild(el('p', 'note', `Solver material: ${report.material.name}`));

  const title = `${meta.study || store.fea.study} · ${FIELD_LABELS[meta.field] || meta.field || ''}`
    + `${meta.revision != null ? ` · r${meta.revision}` : ''} · ${store.fea.quality}`;
  wrap.appendChild(el('div', 'fea-title', title));

  if (legend.title) wrap.appendChild(el('div', 'legend-title', legend.title));
  if (legend.kind === 'bands') {
    for (const band of legend.bands || []) {
      const [color, label] = band;
      const row = el('div', 'band-row');
      const swatch = el('span', 'band-swatch');
      swatch.style.background = cssTriple(color);
      row.append(swatch, el('span', null, label));
      wrap.appendChild(row);
    }
    if (legend.min != null) wrap.appendChild(el('div', 'legend-foot', `min ${fmt(legend.min, 3)}`));
  } else if (legend.kind === 'scale') {
    const max = legend.max != null ? Number(legend.max) : 1;
    const track = el('div', 'scale-track');
    const bar = el('div', 'scale-bar');
    bar.style.background = turboGradient();
    track.appendChild(bar);
    if (Array.isArray(legend.mark) && max > 0) {
      const markValue = Number(legend.mark[0]);
      if (Number.isFinite(markValue)) {
        const mark = el('span', 'scale-mark');
        mark.style.left = `${clamp((markValue / max) * 100, 0, 100)}%`;
        track.appendChild(mark);
      }
    }
    wrap.appendChild(track);
    const labels = el('div', 'scale-labels');
    labels.append(el('span', null, '0'), el('span', null, `max ${fmt(max, 3)}`));
    wrap.appendChild(labels);
    if (Array.isArray(legend.mark)) {
      wrap.appendChild(el('div', 'legend-foot', `${legend.mark[1] || 'mark'} ${fmt(Number(legend.mark[0]), 3)}`));
    }
  }

  const hotspots = Array.isArray(report.hotspots) ? report.hotspots : [];
  if (hotspots.length) {
    const box = el('div', 'hotspots');
    box.appendChild(el('h3', null, `hotspots (${hotspots.length})`));
    for (const hotspot of hotspots) {
      const row = el('button', 'hotspot');
      row.type = 'button';
      const where = hotspot.features && hotspot.features.length
        ? hotspot.features.join(' ')
        : (hotspot.at_support ? 'at support' : (hotspot.at_load ? 'at load' : 'surface'));
      row.append(
        el('span', null, `#${hotspot.id}`),
        el('span', 'hs-where', where),
        el('span', 'hs-value', hotspot.von_mises_MPa != null ? `${fmt(hotspot.von_mises_MPa, 2)} MPa` : ''),
      );
      if (Array.isArray(hotspot.at)) {
        row.title = 'focus the camera on this hotspot';
        row.addEventListener('click', () => focusPoint(hotspot.at));
      }
      box.appendChild(row);
    }
    wrap.appendChild(box);
  }

  const warnings = Array.isArray(report.warnings) ? report.warnings : [];
  for (const warning of warnings) {
    wrap.appendChild(el('div', 'fea-warning', `${warning.code || 'warning'}: ${warning.message || ''}`));
  }

  const clear = el('button', 'wide', 'clear overlay');
  clear.type = 'button';
  clear.addEventListener('click', () => {
    clearFea();
    renderStress();
    updateStatus();
  });
  wrap.appendChild(clear);
  return wrap;
}

async function runFea() {
  const doc = store.doc;
  if (doc?.revision == null || !store.study || store.feaRunning || store.building) return;
  const study = store.study;
  const field = store.feaField;
  const quality = store.feaQuality;
  store.feaRunning = true;
  store.feaError = '';
  clearFea();
  disableBedPreview();
  stopPlay();
  setTimelineStep(timelineSteps().length - 1);
  if (store.print.overlayKind) {
    clearPrintOverlay();
    syncPrintToggles();
  }
  const seq = ++store.feaSeq;
  renderStress();

  try {
    const query = new URLSearchParams({ study, field, quality, rev: String(doc.revision) });
    const response = await fetch(apiPath(`/api/fea?${query}`), { cache: 'no-store' });
    if (!response.ok) {
      let message = `stress analysis HTTP ${response.status}`;
      try {
        const body = await response.json();
        message = (body && body.error && body.error.message) || message;
      } catch { /* not JSON */ }
      throw new Error(message);
    }
    const parsed = parseMesh(await response.arrayBuffer());
    if (seq !== store.feaSeq) return;
    if (!parsed.colors.length) throw new Error('stress overlay has no vertex colours');
    const meta = parsed.header.meta || {};
    if (meta.revision !== doc.revision) throw new Error('The solver returned a different revision. Run the analysis again.');
    const spec = (doc.studies || []).find((item) => item.name === study);
    const partName = spec && spec.part ? spec.part : null;
    const partView = createView(parsed, {
      name: partName,
      color: '#ffffff',
      vertexColors: true,
      opacity: 1,
      pickable: false,
      frameIndex: null,
    });
    view.clipRoot.add(partView.group);
    store.fea = { view: partView, meta, part: partName, study, field, quality };
    applyVisibility();
  } catch (error) {
    if (seq !== store.feaSeq) return;
    store.feaError = String(error.message || error);
    pushNote('error', `stress analysis failed: ${store.feaError}`);
  } finally {
    if (seq === store.feaSeq) {
      store.feaRunning = false;
      renderStress();
      updateStatus();
    }
  }
}

function clearFea() {
  if (!store.fea) return;
  disposeView(store.fea.view);
  store.fea = null;
  applyVisibility();
}

// --------------------------------------------------------------------- print readiness

function partMaterialName(part) {
  if (!part) return null;
  const material = part.material;
  if (typeof material === 'string') return material;
  if (material && typeof material.name === 'string') return material.name;
  const summaryMaterial = part.summary && part.summary.material;
  return typeof summaryMaterial === 'string' ? summaryMaterial : null;
}

function printCaps() {
  const caps = (store.doc && store.doc.capabilities) || {};
  const defaultBed = Array.isArray(caps.default_bed) && caps.default_bed.length === 3
    ? caps.default_bed.map((value) => Number(value))
    : [256, 256, 256];
  return {
    beds: Array.isArray(caps.beds) ? caps.beds.filter((name) => typeof name === 'string') : [],
    materials: Array.isArray(caps.materials) ? caps.materials.filter((name) => typeof name === 'string') : [],
    defaultBed: defaultBed.every((value) => Number.isFinite(value) && value > 0) ? defaultBed : [256, 256, 256],
  };
}

function ensurePrintBed(caps) {
  const beds = caps.beds;
  const fallback = beds.includes('A1') ? 'A1' : (beds[0] || '__custom');
  if (!store.print.bedTouched) {
    store.print.bed = fallback;
  } else if (store.print.bed !== '__custom' && !beds.includes(store.print.bed)) {
    store.print.bed = fallback;
  }
  if (!store.print.customTouched) {
    store.print.custom = {
      x: caps.defaultBed[0],
      y: caps.defaultBed[1],
      z: caps.defaultBed[2],
    };
  }
}

function printBedParam() {
  if (store.print.bed === '__custom') {
    const { x, y, z } = store.print.custom;
    if (![x, y, z].every((value) => Number.isFinite(value) && value > 0)) return null;
    return `${x},${y},${z}`;
  }
  return store.print.bed || null;
}

function printRequestKey() {
  return [
    store.revision, activePartName(), store.print.material || '',
    printBedParam() || '', store.print.angle, store.print.nozzle,
  ].join('|');
}

function printDataForContext() {
  const part = activePartName();
  const print = store.print;
  if (!print.data || print.dataPart !== part || print.dataRevision !== store.revision) return null;
  return print.data;
}

function printDataForCurrent() {
  const data = printDataForContext();
  return data && store.print.dataKey === printRequestKey() ? data : null;
}

function syncPrintContext() {
  const doc = store.doc;
  const part = activePartName();
  const partData = part && doc && doc.parts ? doc.parts[part] : null;
  const revision = doc ? doc.revision : null;
  const caps = printCaps();
  ensurePrintBed(caps);

  let materialChanged = false;
  if (store.print.forPart !== part) {
    store.print.forPart = part;
    store.print.followPartMaterial = true;
    store.print.material = partMaterialName(partData) || 'PLA';
    materialChanged = true;
  } else if (store.print.followPartMaterial) {
    const next = partMaterialName(partData) || 'PLA';
    if (next !== store.print.material) {
      store.print.material = next;
      materialChanged = true;
    }
  }
  if (!store.print.material) store.print.material = 'PLA';

  const key = part && revision != null ? `${revision}|${part}` : '';
  let contextChanged = false;
  if (store.print.contextKey !== key) {
    store.print.contextKey = key;
    store.print.data = null;
    store.print.dataKey = '';
    store.print.dataPart = null;
    store.print.dataRevision = null;
    store.print.error = '';
    store.print.loading = false;
    store.print.seq += 1;
    clearPrintOverlay();
    contextChanged = true;
  }
  if ((contextChanged || materialChanged) && key) schedulePrintFetch();
}

function schedulePrintFetch() {
  if (store.print.timer) clearTimeout(store.print.timer);
  store.print.timer = setTimeout(() => {
    store.print.timer = 0;
    fetchPrint();
  }, 250);
}

async function fetchPrint() {
  if (store.print.timer) {
    clearTimeout(store.print.timer);
    store.print.timer = 0;
  }
  const part = activePartName();
  const revision = store.revision;
  if (!store.doc || revision == null || !part) {
    store.print.loading = false;
    store.print.error = '';
    store.print.data = null;
    store.print.dataKey = '';
    renderPrintPanel();
    return;
  }
  const key = printRequestKey();
  const seq = ++store.print.seq;
  store.print.loading = true;
  renderPrintPanel();

  const query = new URLSearchParams();
  query.set('part', part);
  query.set('rev', String(revision));
  if (store.print.material) query.set('material', store.print.material);
  const bed = printBedParam();
  if (bed) query.set('bed', bed);
  query.set('angle', String(store.print.angle));
  query.set('nozzle', String(store.print.nozzle));

  let request;
  request = (async () => {
    const response = await fetch(apiPath(`/api/print?${query.toString()}`), { cache: 'no-store' });
    const body = await response.json().catch(() => null);
    if (seq !== store.print.seq) return;
    if (!response.ok || !body || body.ok === false) {
      const error = (body && body.error) || {
        code: 'PRINT_FAILED',
        message: `print check failed (HTTP ${response.status})`,
      };
      store.print.error = error.message || error.code || 'print check failed';
      store.print.loading = false;
      renderPrintPanel();
      return;
    }
    store.print.data = body;
    store.print.dataKey = key;
    store.print.dataPart = part;
    store.print.dataRevision = revision;
    store.print.error = '';
    store.print.loading = false;
    renderPrintPanel();
    refreshPrintOverlay();
  })().catch((error) => {
    if (seq !== store.print.seq) return;
    store.print.error = String(error.message || error);
    store.print.loading = false;
    renderPrintPanel();
  }).finally(() => {
    if (store.print.promise === request) store.print.promise = null;
  });
  store.print.promise = request;
  return request;
}

function renderPrintPanel() {
  renderPrintControls();
  renderPrintStatus();
  renderPrintResults();
  renderPrintFaceAction();
  renderBedPreview();
  renderPrintDiagnostics();
  syncPrintToggles();
}

function renderPrintControls() {
  const host = byId('print-controls');
  if (!host) return;
  const doc = store.doc;
  const part = activePartName();
  const partData = part && doc && doc.parts ? doc.parts[part] : null;
  const caps = printCaps();
  ensurePrintBed(caps);
  if (!doc || doc.revision == null || !part) {
    host.dataset.signature = '';
    clearNode(host);
    return;
  }

  const partMaterial = partMaterialName(partData) || '';
  const signature = [
    part, partMaterial, caps.beds.join('|'), caps.materials.join('|'), caps.defaultBed.join(','),
  ].join('::');
  if (host.dataset.signature === signature) {
    const materialSelect = byId('print-material');
    if (materialSelect && store.print.material) materialSelect.value = store.print.material;
    const bedSelect = byId('print-bed');
    if (bedSelect && store.print.bed) bedSelect.value = store.print.bed;
    const customRow = byId('print-bed-custom');
    if (customRow) customRow.classList.toggle('hidden', store.print.bed !== '__custom');
    return;
  }
  host.dataset.signature = signature;
  clearNode(host);

  // material
  const materialNames = [];
  for (const name of caps.materials) if (!materialNames.includes(name)) materialNames.push(name);
  if (partMaterial && !materialNames.includes(partMaterial)) materialNames.push(partMaterial);
  if (store.print.material && !materialNames.includes(store.print.material)) materialNames.push(store.print.material);
  if (!materialNames.length) materialNames.push('PLA');

  const materialRow = el('div', 'row');
  const materialLabel = el('label', null, 'material');
  materialLabel.htmlFor = 'print-material';
  materialRow.appendChild(materialLabel);
  const materialSelect = document.createElement('select');
  materialSelect.id = 'print-material';
  for (const name of materialNames) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    materialSelect.appendChild(option);
  }
  materialSelect.value = store.print.material || materialNames[0];
  materialSelect.addEventListener('change', () => {
    store.print.followPartMaterial = false;
    store.print.material = materialSelect.value;
    schedulePrintFetch();
  });
  materialRow.appendChild(materialSelect);
  host.appendChild(materialRow);

  // bed
  const bedRow = el('div', 'row');
  const bedLabel = el('label', null, 'bed');
  bedLabel.htmlFor = 'print-bed';
  bedRow.appendChild(bedLabel);
  const bedSelect = document.createElement('select');
  bedSelect.id = 'print-bed';
  for (const name of caps.beds) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    bedSelect.appendChild(option);
  }
  const customOption = document.createElement('option');
  customOption.value = '__custom';
  customOption.textContent = 'Custom…';
  bedSelect.appendChild(customOption);
  bedSelect.value = store.print.bed || '__custom';
  bedRow.appendChild(bedSelect);
  host.appendChild(bedRow);

  const customRow = el('div', 'row');
  customRow.id = 'print-bed-custom';
  customRow.appendChild(el('span', 'grow', ''));
  const bedInputs = el('div', 'bed-inputs');
  for (const axis of ['x', 'y', 'z']) {
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '1';
    input.step = '1';
    input.value = String(store.print.custom[axis]);
    input.setAttribute('aria-label', `bed ${axis} in mm`);
    input.addEventListener('input', () => {
      const value = Number(input.value);
      if (!Number.isFinite(value) || value <= 0) return;
      store.print.custom[axis] = value;
      store.print.customTouched = true;
      schedulePrintFetch();
    });
    bedInputs.appendChild(input);
    if (axis !== 'z') bedInputs.appendChild(el('span', null, '×'));
  }
  bedInputs.appendChild(el('span', null, 'mm'));
  customRow.appendChild(bedInputs);
  host.appendChild(customRow);
  const updateCustomVisibility = () => customRow.classList.toggle('hidden', bedSelect.value !== '__custom');
  bedSelect.addEventListener('change', () => {
    store.print.bed = bedSelect.value;
    store.print.bedTouched = true;
    updateCustomVisibility();
    schedulePrintFetch();
  });
  updateCustomVisibility();

  // overhang threshold
  const angleRow = el('div', 'row');
  const angleLabel = el('label', null, 'overhang');
  angleLabel.htmlFor = 'print-angle';
  angleRow.appendChild(angleLabel);
  const angleWrap = el('div', 'range-wrap');
  const angleInput = document.createElement('input');
  angleInput.type = 'range';
  angleInput.id = 'print-angle';
  angleInput.min = '30';
  angleInput.max = '70';
  angleInput.step = '1';
  angleInput.value = String(store.print.angle);
  const angleValue = el('span', 'range-value', `${fmt(store.print.angle, 0)}°`);
  angleValue.id = 'print-angle-value';
  angleInput.addEventListener('input', () => {
    store.print.angle = Number(angleInput.value);
    setText(angleValue, `${fmt(store.print.angle, 0)}°`);
    schedulePrintFetch();
  });
  angleWrap.append(angleInput, angleValue);
  angleRow.appendChild(angleWrap);
  host.appendChild(angleRow);

  // nozzle
  const nozzleRow = el('div', 'row');
  const nozzleLabel = el('label', null, 'nozzle');
  nozzleLabel.htmlFor = 'print-nozzle';
  nozzleRow.appendChild(nozzleLabel);
  const nozzleInput = document.createElement('input');
  nozzleInput.type = 'number';
  nozzleInput.id = 'print-nozzle';
  nozzleInput.min = '0.2';
  nozzleInput.max = '1.5';
  nozzleInput.step = '0.05';
  nozzleInput.value = String(store.print.nozzle);
  nozzleInput.addEventListener('input', () => {
    const value = Number(nozzleInput.value);
    if (!Number.isFinite(value) || value <= 0) return;
    store.print.nozzle = value;
    schedulePrintFetch();
  });
  nozzleRow.append(nozzleInput, el('span', 'num-unit', 'mm'));
  host.appendChild(nozzleRow);
}

function renderPrintStatus() {
  setText(byId('print-state'), store.print.loading ? 'updating…' : '');
  const host = byId('print-note');
  if (!host) return;
  clearNode(host);
  const doc = store.doc;
  const part = activePartName();
  if (!doc || doc.revision == null || !part) return;
  if (store.print.error) {
    host.appendChild(el('div', 'print-note bad', store.print.error));
    return;
  }
  if (store.visibility.get(part) === false) {
    host.appendChild(el('div', 'print-note', 'part is hidden in the viewport'));
  }
}

function printBedLabel(data) {
  const size = data && data.bed && Array.isArray(data.bed.size_mm) ? data.bed.size_mm : null;
  const dims = size ? size.map((value) => fmt(value, 0)).join(' × ') : '';
  if (store.print.bed === '__custom' || !store.print.bed) return dims ? `${dims} mm` : 'bed';
  return `${store.print.bed}${dims ? ` (${dims} mm)` : ''}`;
}

function printBedOverflow(data) {
  const bbox = data && data.geometry && Array.isArray(data.geometry.bbox_mm)
    ? data.geometry.bbox_mm.map(Number)
    : null;
  const bed = data && data.bed && Array.isArray(data.bed.size_mm) ? data.bed.size_mm.map(Number) : null;
  if (!bbox || !bed || bbox.length !== 3 || bed.length !== 3) return '';
  return bbox.map((value, index) => (
    value - bed[index] > 1e-6 ? `${['x', 'y', 'z'][index]} +${fmt(value - bed[index], 1)}` : null
  )).filter(Boolean).join(', ');
}

function printFaceRef(face) {
  const refs = Array.isArray(face && face.refs) ? face.refs.filter(Boolean) : [];
  return refs.length ? refs[0] : `face #${face.face}`;
}

function printFaceTitle(face) {
  const refs = Array.isArray(face && face.refs) ? face.refs.filter(Boolean) : [];
  return `face #${face.face}${refs.length ? ` — ${refs.join(', ')}` : ''}`;
}

function renderPrintResults() {
  const host = byId('print-results');
  if (!host) return;
  clearNode(host);
  const doc = store.doc;
  const part = activePartName();
  if (!doc) {
    host.appendChild(el('div', 'note', 'connecting'));
    return;
  }
  if (doc.revision == null) {
    host.appendChild(el('div', 'note', 'no revision yet'));
    return;
  }
  if (!part) {
    host.appendChild(el('div', 'note', 'no part'));
    return;
  }
  const data = printDataForContext();
  if (!data) {
    if (store.print.loading) {
      const row = el('div', 'fea-status');
      row.append(el('span', 'spinner'), el('span', null, 'checking print readiness…'));
      host.appendChild(row);
    } else if (!store.print.error) {
      host.appendChild(el('div', 'note', 'no print data'));
    }
    return;
  }

  const geometry = data.geometry || {};
  const bbox = Array.isArray(geometry.bbox_mm) ? geometry.bbox_mm.map(Number) : null;
  const mass = Number(data.mass_g);
  const summary = el('div', 'print-summary');
  summary.appendChild(el('span', 'print-mass', Number.isFinite(mass) ? `${fmt(mass, 2)} g` : '-'));
  if (geometry.volume_mm3 != null) summary.appendChild(el('span', 'print-sub', `${fmt(geometry.volume_mm3, 0)} mm³`));
  const materialName = data.material && data.material.name;
  summary.appendChild(el('span', 'print-sub', materialName ? `estimate for ${materialName}` : 'estimate'));
  host.appendChild(summary);

  const stats = el('div', 'print-stats');
  const statRows = [];
  if (bbox) statRows.push(['bbox', `${bbox.map((value) => fmt(value, 1)).join(' × ')} mm`]);
  if (data.estimate && data.estimate.time_min != null) {
    statRows.push(['rough time', `${fmt(data.estimate.time_min, 1)} min`]);
  }
  for (const [key, value] of statRows) {
    stats.append(el('span', null, key), el('span', null, value));
  }
  host.appendChild(stats);

  const fits = Boolean(data.bed && data.bed.fits);
  const overflow = printBedOverflow(data);
  const bed = el('div', `cad-status fit-badge ${fits ? 'ok' : 'bad'}`);
  bed.dataset.tone = fits ? 'success' : 'error';
  bed.textContent = fits
    ? `fits ${printBedLabel(data)}`
    : `does not fit ${printBedLabel(data)}${overflow ? ` · ${overflow} mm` : ''}`;
  host.appendChild(bed);

  const orientation = data.orientation || {};
  const bedFits = Boolean(data.bed && data.bed.fits);
  // only useful when the part does not fit where it is
  if (!bedFits && Array.isArray(orientation.fits_if_rotated) && orientation.fits_if_rotated.length) {
    const current = bbox;
    const alternatives = orientation.fits_if_rotated.filter((candidate) => (
      Array.isArray(candidate) && candidate.length === 3
      && (!current || candidate.some((value, index) => Math.abs(Number(value) - current[index]) > 1e-6))
    ));
    if (alternatives.length) {
      const candidate = alternatives[0].map((value) => fmt(value, 1)).join(' × ');
      host.appendChild(el('div', 'print-note',
        `an axis-aligned orientation fits: ${candidate} mm — set it in the script with print_on(...)`));
    }
  }
  for (const warning of data.warnings || []) {
    const unset = warning.code === 'NO_PRINT_ORIENTATION';
    const row = el('div', 'print-warn');
    if (unset) row.appendChild(el('strong', 'warn-title', 'Print orientation is unset'));
    row.appendChild(el('span', 'warn-msg', unset
      ? 'Checks use the design pose. Select a face to preview it against the bed.'
      : warning.message || warning.code || 'Print warning'));
    if (warning.hint) row.appendChild(el('span', 'warn-hint', warning.hint));
    row.title = warning.code || 'Print warning';
    if (unset) {
      const draft = el('button', 'print-chat-draft', 'Draft fix in chat');
      draft.type = 'button';
      draft.addEventListener('click', () => draftInChat(
        `Help me choose a print orientation for "${part}". Explain which face should go on the bed.`,
      ));
      row.appendChild(draft);
    }
    host.appendChild(row);
  }

  const thin = data.thin || {};
  const thinFaces = Array.isArray(thin.faces) ? thin.faces : [];
  const thinBox = el('div', 'print-list');
  const thinHead = el('h3');
  const thinMeta = [
    thin.count != null ? String(thin.count) : String(thinFaces.length),
    thin.threshold_mm != null ? `< ${fmt(thin.threshold_mm, 2)} mm` : '',
    thin.min_mm != null ? `min ${fmt(thin.min_mm, 2)} mm` : '',
  ].filter(Boolean).join(' · ');
  thinHead.append(el('span', null, 'thin walls'), el('span', 'count', thinMeta));
  thinBox.appendChild(thinHead);
  if (thinFaces.length) {
    for (const face of thinFaces) {
      const row = el('button', 'print-row');
      row.type = 'button';
      row.append(
        el('span', 'pr-metric', face.thickness != null ? `${fmt(face.thickness, 2)} mm` : '-'),
        el('span', 'pr-where', printFaceRef(face)),
      );
      row.title = printFaceTitle(face);
      row.addEventListener('click', () => selectPrintFace(part, face.face, face.at));
      thinBox.appendChild(row);
    }
  } else {
    thinBox.appendChild(el('div', 'print-note', 'none below the threshold'));
  }
  host.appendChild(thinBox);

  const overhang = data.overhang || {};
  const overFaces = Array.isArray(overhang.faces) ? overhang.faces : [];
  const overBox = el('div', 'print-list');
  const overHead = el('h3');
  overHead.append(
    el('span', null, 'overhangs'),
    el('span', 'count', `${overhang.count != null ? overhang.count : overFaces.length} · ${fmt(overhang.area_mm2 || 0, 1)} mm²`),
  );
  overBox.appendChild(overHead);
  if (overFaces.length) {
    for (const face of overFaces) {
      const row = el('button', 'print-row over');
      row.type = 'button';
      row.append(
        el('span', 'pr-metric', face.angle_deg != null ? `${fmt(face.angle_deg, 0)}°` : '-'),
        el('span', 'pr-metric', face.area != null ? `${fmt(face.area, 1)} mm²` : '-'),
        el('span', 'pr-where', printFaceRef(face)),
      );
      row.title = printFaceTitle(face);
      row.addEventListener('click', () => selectPrintFace(part, face.face, null));
      overBox.appendChild(row);
    }
  } else {
    overBox.appendChild(el('div', 'print-note', 'nothing overhangs the threshold'));
  }
  host.appendChild(overBox);
}

function selectPrintFace(part, faceId, at) {
  if (!Number.isInteger(faceId)) return;
  const point = Array.isArray(at) && at.length === 3 ? new THREE.Vector3(at[0], at[1], at[2]) : null;
  selectFace({ part, faceId, point });
}

// --------------------------------------------------------------------- bed preview

function previewTarget() {
  const selection = store.selection;
  if (!selection || selection.faceId == null) return null;
  const refs = selection.info && Array.isArray(selection.info.refs)
    ? selection.info.refs.filter(Boolean)
    : [];
  return { part: selection.part, faceId: selection.faceId, ref: refs[0] || null };
}

function faceNormalFor(part, faceId) {
  const partView = part ? store.revisionGroups.get(part) : null;
  if (!partView || faceId == null || !partView.parsed) return null;
  const parsed = partView.parsed;
  if (!parsed.faceIds.length || !parsed.indices.length) return null;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const ab = new THREE.Vector3();
  const ac = new THREE.Vector3();
  const normal = new THREE.Vector3();
  const accumulated = new THREE.Vector3();
  const centroid = new THREE.Vector3();
  let points = 0;
  let triangles = 0;
  for (let t = 0; t < parsed.faceIds.length; t += 1) {
    if (!matches(parsed.faceIds[t])) continue;
    for (let k = 0; k < 3; k += 1) {
      const index = parsed.indices[t * 3 + k] * 3;
      const x = parsed.positions[index];
      const y = parsed.positions[index + 1];
      const z = parsed.positions[index + 2];
      if (k === 0) a.set(x, y, z);
      else if (k === 1) b.set(x, y, z);
      else c.set(x, y, z);
      centroid.x += x;
      centroid.y += y;
      centroid.z += z;
      points += 1;
    }
    ab.subVectors(b, a);
    ac.subVectors(c, a);
    normal.crossVectors(ab, ac);
    if (normal.lengthSq() > 1e-18) {
      accumulated.add(normal); // length is twice the triangle area
      triangles += 1;
    }
  }
  if (!triangles || accumulated.lengthSq() < 1e-18) return null;
  accumulated.normalize();
  centroid.multiplyScalar(1 / Math.max(points, 1));
  if (partView.box && !partView.box.isEmpty()) {
    const centre = partView.box.getCenter(new THREE.Vector3());
    if (accumulated.dot(centroid.sub(centre)) < 0) accumulated.negate();
  }
  return accumulated;
}

function captureCamera() {
  return {
    position: view.camera.position.clone(),
    up: view.camera.up.clone(),
    target: view.controls.target.clone(),
    mode: view.mode,
  };
}

function restoreCamera(saved) {
  if (!saved || !view.camera || !view.controls) return;
  view.camera.up.copy(saved.up);
  view.camera.position.copy(saved.position);
  view.camera.lookAt(saved.target);
  view.controls.target.copy(saved.target);
  view.controls.update();
}

function orientCameraToFace(normal) {
  if (!normal || !view.camera || !view.controls) return;
  const target = view.controls.target.clone();
  const q = new THREE.Quaternion().setFromUnitVectors(
    normal.clone().normalize(), new THREE.Vector3(0, 0, -1),
  );
  const inverse = q.clone().invert();
  const offset = view.camera.position.clone().sub(target).applyQuaternion(inverse);
  if (offset.lengthSq() < 1e-9) offset.set(0, 0, Math.max(view.sceneRadius * 2, 10));
  const up = view.camera.up.clone().applyQuaternion(inverse);
  view.camera.up.copy(up);
  view.camera.position.copy(target).add(offset);
  view.camera.lookAt(target);
  view.controls.target.copy(target);
  view.controls.update();
}

function clearBedPlane() {
  const bed = store.bedPreview.bed;
  if (!bed) return;
  bed.removeFromParent();
  disposeObject(bed);
  store.bedPreview.bed = null;
}

function buildBedPlane(normal) {
  const bp = store.bedPreview;
  const partView = bp.part ? store.revisionGroups.get(bp.part) : null;
  if (!partView || !partView.box || partView.box.isEmpty()) return;
  const box = partView.box;
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  const n = normal.clone().normalize();
  // The contact face normal points toward the bed, so the lowest point in the
  // build frame is the one furthest along n.
  let contact = -Infinity;
  for (let i = 0; i < 8; i += 1) {
    const corner = new THREE.Vector3(
      i & 1 ? box.max.x : box.min.x,
      i & 2 ? box.max.y : box.min.y,
      i & 4 ? box.max.z : box.min.z,
    );
    contact = Math.max(contact, corner.dot(n));
  }
  if (!Number.isFinite(contact)) return;
  const position = centre.clone().addScaledVector(n, contact - centre.dot(n));
  const planeSize = Math.max(size.length() * 1.25, 20);

  const group = new THREE.Group();
  const fillMaterial = new THREE.MeshBasicMaterial({
    color: 0x4f7fae, transparent: true, opacity: 0.11,
    depthWrite: false, side: THREE.DoubleSide,
  });
  trackMaterial(fillMaterial);
  const fill = new THREE.Mesh(new THREE.PlaneGeometry(planeSize, planeSize), fillMaterial);
  fill.renderOrder = 3;
  group.add(fill);

  const grid = new THREE.GridHelper(planeSize, 12, 0x4a6a8a, 0x27333f);
  grid.rotation.x = Math.PI / 2;
  grid.material.transparent = true;
  grid.material.opacity = 0.32;
  grid.material.depthWrite = false;
  trackMaterial(grid.material);
  group.add(grid);

  const edgeGeometry = new THREE.EdgesGeometry(new THREE.PlaneGeometry(planeSize, planeSize));
  const edgeMaterial = new THREE.LineBasicMaterial({
    color: 0x7aa2d6, transparent: true, opacity: 0.55,
  });
  trackMaterial(edgeMaterial);
  group.add(new THREE.LineSegments(edgeGeometry, edgeMaterial));

  group.quaternion.copy(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n));
  group.position.copy(position);
  view.scene.add(group);
  bp.bed = group;
}

function enableBedPreview(target) {
  const bp = store.bedPreview;
  const source = target || previewTarget();
  if (!source || source.faceId == null) return false;
  const normal = faceNormalFor(source.part, source.faceId);
  if (!normal) {
    bp.error = 'cannot compute a normal for this face';
    renderBedPreview();
    return false;
  }
  if (!bp.active) bp.prev = captureCamera();
  bp.active = true;
  bp.part = source.part;
  bp.faceId = source.faceId;
  bp.ref = source.ref || null;
  bp.normal = normal;
  bp.error = '';
  clearBedPlane();
  buildBedPlane(normal);
  orientCameraToFace(normal);
  renderBedPreview();
  return true;
}

function disableBedPreview(restore = true) {
  const bp = store.bedPreview;
  const wasActive = bp.active;
  bp.active = false;
  bp.error = '';
  clearBedPlane();
  if (restore && wasActive && bp.prev) restoreCamera(bp.prev);
  bp.prev = null;
  renderBedPreview();
}

function refreshBedPreviewAfterGeometry() {
  const bp = store.bedPreview;
  if (!bp.active) return;
  if (!bp.part || !store.revisionGroups.has(bp.part)) {
    disableBedPreview(false);
    return;
  }
  const normal = faceNormalFor(bp.part, bp.faceId);
  if (normal) {
    const changed = !bp.normal || normal.dot(bp.normal) < 0.999;
    bp.normal = normal;
    if (changed) orientCameraToFace(normal);
  }
  clearBedPlane();
  if (bp.normal) buildBedPlane(bp.normal);
  renderBedPreview();
  // re-attach the selection to the same named face after a revision bump
  if (bp.ref) {
    faceTable(bp.part, store.revision).then((table) => {
      if (!bp.active) return;
      let info = null;
      for (const face of table.values()) {
        if (Array.isArray(face.refs) && face.refs.includes(bp.ref)) { info = face; break; }
      }
      if (!info) return;
      bp.faceId = info.id;
      if (store.selection && store.selection.part === bp.part && store.selection.faceId === info.id) {
        store.selection.info = info;
        renderSelection();
        return;
      }
      store.selection = {
        part: bp.part,
        faceId: info.id,
        point: Array.isArray(info.center)
          ? new THREE.Vector3(info.center[0], info.center[1], info.center[2])
          : null,
        info,
        error: '',
      };
      store.faceSeq += 1;
      renderSelection();
      updateHighlights();
    }).catch(() => { /* face table is optional for the preview */ });
  }
}

async function applyOrientation(button) {
  const bp = store.bedPreview;
  const source = bp.active
    ? { part: bp.part, ref: bp.ref }
    : previewTarget();
  if (!source || !source.part || !source.ref) {
    bp.error = 'name the face in the script first';
    renderBedPreview();
    return;
  }
  bp.busy = true;
  bp.error = '';
  if (isBrowsingHistory()) {
    bp.busy = false;
    bp.error = `viewing r${store.viewRevision} — return to the current revision to change the model`;
    renderBedPreview();
    return;
  }
  if (button) button.disabled = true;
  renderBedPreview();
  try {
    const body = await postJson(apiPath('/api/orient'), { part: source.part, ref: source.ref,
                                                  viewing: store.viewRevision });
    const revision = body && body.revision != null ? `r${body.revision}` : 'a new revision';
    pushNote('ok', `orientation applied: ${source.part} on ${source.ref} (${revision})`);
    showToast(`oriented on ${source.ref} — ${revision}`);
    await refreshState();
    if (bp.active) refreshBedPreviewAfterGeometry();
  } catch (error) {
    bp.error = error.message || String(error);
    pushNote('error', `orientation failed: ${bp.error}`);
    showToast(`orientation failed: ${bp.error}`);
  } finally {
    bp.busy = false;
    renderBedPreview();
  }
}

function renderBedPreview() {
  const host = byId('print-bed-preview');
  if (!host) return;
  const hud = byId('bed-preview-hud');
  const bpState = store.bedPreview;
  if (hud) {
    const visible = bpState.active && bpState.faceId != null;
    hud.classList.toggle('hidden', !visible);
    if (visible) {
      const refLabel = bpState.ref || `face #${bpState.faceId}`;
      clearNode(hud);
      hud.append(el('span', 'dim', 'bed preview: '), el('span', null, refLabel));
      hud.title = `bed preview: ${refLabel}`;
    } else {
      setText(hud, '');
    }
  }
  const bp = store.bedPreview;
  const target = bp.active ? { part: bp.part, faceId: bp.faceId, ref: bp.ref } : previewTarget();
  const signature = JSON.stringify([
    bp.active, bp.busy, bp.error, bp.ref, bp.faceId,
    target ? target.part : null, target ? target.faceId : null, target ? target.ref : null,
    store.doc ? store.doc.revision : null,
  ]);
  if (host.dataset.signature === signature) return;
  host.dataset.signature = signature;
  clearNode(host);

  if (!target || target.faceId == null) {
    host.appendChild(el('div', 'bed-preview-note', 'select a face to preview the print orientation'));
    return;
  }

  const head = el('div', 'bed-preview-head');
  const label = document.createElement('label');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.id = 'print-preview-on';
  checkbox.checked = bp.active;
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) {
      if (!enableBedPreview()) checkbox.checked = false;
    } else {
      disableBedPreview(true);
    }
  });
  label.append(checkbox, document.createTextNode('bed preview'));
  head.appendChild(label);
  host.appendChild(head);

  const refText = target.ref || `face #${target.faceId}`;
  const readout = el('div', 'bed-preview-readout');
  readout.append(document.createTextNode(bp.active ? 'bed preview: ' : 'selected: '));
  readout.appendChild(el('span', bp.active ? null : 'dim', refText));
  host.appendChild(readout);

  const actions = el('div', 'bed-preview-actions');
  const apply = el('button', null, bp.busy ? 'applying…' : 'Apply orientation');
  apply.type = 'button';
  apply.disabled = bp.busy || !target.ref;
  apply.title = target.ref
    ? `add part.print_on("${target.ref}") and rebuild`
    : 'name the face in the script first';
  apply.addEventListener('click', () => applyOrientation(apply));
  const copy = el('button', null, target.ref ? `copy p.print_on("${target.ref}")` : 'copy p.print_on(...)');
  copy.type = 'button';
  copy.title = 'copy the print orientation line';
  copy.addEventListener('click', () => printOnSelectedFace(copy));
  actions.append(apply, copy);
  host.appendChild(actions);

  if (!target.ref) host.appendChild(el('div', 'bed-preview-note', 'name the face in the script first'));
  if (bp.error) host.appendChild(el('div', 'bed-preview-error', bp.error));
  else if (bp.active) host.appendChild(el('div', 'bed-preview-note', `${refText} faces the bed`));
}

function renderPrintFaceAction() {
  const host = byId('print-face-action');
  if (!host) return;
  const selection = store.selection;
  const refs = selection && selection.info && Array.isArray(selection.info.refs)
    ? selection.info.refs.filter(Boolean)
    : [];
  const signature = selection && selection.faceId != null
    ? `${selection.part}|${selection.faceId}|${refs.join(',')}`
    : '';
  if (host.dataset.signature === signature) return;
  host.dataset.signature = signature;
  clearNode(host);
  if (!selection || selection.faceId == null) return;

  const wrap = el('div', 'print-face');
  wrap.appendChild(el('div', 'tiny dim', 'print on this face'));
  const ref = refs.length ? refs[0] : null;
  const button = el('button', null, ref ? `copy p.print_on("${ref}")` : 'copy print orientation');
  button.type = 'button';
  button.title = `${selection.part} · face #${selection.faceId}`;
  button.addEventListener('click', () => printOnSelectedFace(button));
  wrap.appendChild(button);
  host.appendChild(wrap);
}

async function printOnSelectedFace(button) {
  const selection = store.selection;
  const bp = store.bedPreview;
  const part = selection ? selection.part : (bp.active ? bp.part : null);
  const faceId = selection ? selection.faceId : (bp.active ? bp.faceId : null);
  if (!part || faceId == null) return;
  let info = selection && selection.faceId === faceId ? selection.info : null;
  if (!info) {
    try {
      const table = await faceTable(part, store.revision);
      info = table.get(faceId) || null;
      if (selection && store.selection === selection) selection.info = info;
      if (bp.active && bp.faceId === faceId) {
        const refs = info && Array.isArray(info.refs) ? info.refs.filter(Boolean) : [];
        if (refs.length) bp.ref = refs[0];
      }
    } catch { /* fall through to the comment form */ }
  }
  const refs = info && Array.isArray(info.refs) ? info.refs.filter(Boolean) : [];
  let line;
  if (refs.length) {
    line = `p.print_on("${refs[0]}")`;
  } else {
    const center = info && Array.isArray(info.center) ? info.center : null;
    const point = center
      || (selection && selection.point ? [selection.point.x, selection.point.y, selection.point.z] : null);
    const coords = point
      ? point.map((value) => fmtNumber(value, 3)).join(', ')
      : '?, ?, ?';
    line = `# print orientation: face at (${coords})`;
  }
  const copied = await writeClipboard(line);
  if (!copied) {
    pushNote('error', 'clipboard unavailable');
    return;
  }
  showToast('copied');
  if (button) {
    setText(button, 'copied');
    setTimeout(() => {
      const host = byId('print-face-action');
      if (host) host.dataset.signature = '';
      renderPrintFaceAction();
      const preview = byId('print-bed-preview');
      if (preview) preview.dataset.signature = '';
      renderBedPreview();
    }, 1200);
  }
}

function syncPrintToggles() {
  const thin = byId('print-thin');
  const over = byId('print-overhang');
  const data = printDataForCurrent();
  if (thin) {
    thin.checked = store.print.overlayKind === 'thin';
    thin.disabled = !data;
  }
  if (over) {
    over.checked = store.print.overlayKind === 'overhang';
    over.disabled = !data;
  }
}

function faceSetHighlight(parsed, faceIdSet, color, opacity) {
  if (!faceIdSet.size || !parsed.faceIds.length || !parsed.indices.length) return null;
  let count = 0;
  for (let triangle = 0; triangle < parsed.faceIds.length; triangle += 1) {
    if (faceIdSet.has(parsed.faceIds[triangle])) count += 1;
  }
  if (!count) return null;
  const vertices = new Float32Array(count * 9);
  let offset = 0;
  for (let triangle = 0; triangle < parsed.faceIds.length; triangle += 1) {
    if (!faceIdSet.has(parsed.faceIds[triangle])) continue;
    for (let corner = 0; corner < 3; corner += 1) {
      const index = parsed.indices[triangle * 3 + corner] * 3;
      vertices[offset] = parsed.positions[index];
      vertices[offset + 1] = parsed.positions[index + 1];
      vertices[offset + 2] = parsed.positions[index + 2];
      offset += 3;
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  const material = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity,
    depthWrite: false,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -4,
    polygonOffsetUnits: -4,
  });
  trackMaterial(material);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = 6;
  return { mesh, material };
}

function clearPrintOverlay() {
  const overlay = store.print.overlay;
  if (overlay) {
    if (overlay.mesh.parent) overlay.mesh.parent.remove(overlay.mesh);
    overlay.mesh.geometry.dispose();
    view.materials.delete(overlay.material);
    overlay.material.dispose();
    store.print.overlay = null;
  }
  store.print.overlayKind = null;
}

function applyPrintOverlay(kind) {
  clearPrintOverlay();
  if (!kind) {
    syncPrintToggles();
    return;
  }
  store.print.overlayKind = kind;
  const data = printDataForCurrent();
  const part = activePartName();
  const partView = part ? store.revisionGroups.get(part) : null;
  if (!data || !partView) {
    syncPrintToggles();
    return;
  }
  const faces = kind === 'thin'
    ? (data.thin && Array.isArray(data.thin.faces) ? data.thin.faces : [])
    : (data.overhang && Array.isArray(data.overhang.faces) ? data.overhang.faces : []);
  const ids = new Set(faces.map((face) => face.face).filter((id) => Number.isInteger(id)));
  const highlight = faceSetHighlight(partView.parsed, ids, kind === 'thin' ? 0xd2604f : 0xd9a441, 0.55);
  if (highlight) {
    partView.group.add(highlight.mesh);
    store.print.overlay = { kind, mesh: highlight.mesh, material: highlight.material, part };
  }
  syncPrintToggles();
}

function refreshPrintOverlay() {
  const kind = store.print.overlayKind;
  if (!kind) {
    syncPrintToggles();
    return;
  }
  if (store.fea) {
    clearPrintOverlay();
    syncPrintToggles();
    return;
  }
  applyPrintOverlay(kind);
}

// --------------------------------------------------------------------- export

function exportUrl(format, partName) {
  const query = new URLSearchParams();
  query.set('format', format);
  query.set('rev', store.revision == null ? 'head' : String(store.revision));
  if (format !== '3mf' && partName) query.set('part', partName);
  return apiPath(`/api/export?${query.toString()}`);
}

async function exportPart(format) {
  const doc = store.doc;
  if (!doc || doc.revision == null || store.building) return;
  const partName = activePartName();
  if (format !== '3mf' && !partName) {
    showBanner({ code: 'EXPORT_FAILED', message: 'no part selected to export' });
    return;
  }
  const url = exportUrl(format, partName);
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch { /* not JSON */ }
      const error = (payload && payload.error) || {
        code: 'EXPORT_FAILED',
        message: `export failed (HTTP ${response.status})`,
      };
      showBanner(error);
      pushNote('error', `export failed: ${error.message || error.code}`);
      return;
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    const fallback = `${sanitizeName(doc.project || 'cad')}-r${doc.revision}.${format === '3mf' ? '3mf' : format === 'stl' ? 'stl' : 'step'}`;
    let filename = fallback;
    if (match && match[1]) {
      try {
        filename = decodeURIComponent(match[1]);
      } catch {
        filename = match[1];
      }
    }
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 3000);
    pushNote('ok', `exported ${filename}`);
  } catch (error) {
    showBanner({ code: 'EXPORT_FAILED', message: String(error.message || error) });
  }
}

function updateExportControls() {
  const disabled = !store.doc || store.doc.revision == null || store.building;
  for (const id of ['export-stl', 'export-step', 'export-3mf']) {
    const button = byId(id);
    if (button) button.disabled = disabled;
  }
  const note = byId('export-note');
  if (!note) return;
  if (!store.doc || store.doc.revision == null) setText(note, 'no revision');
  else if (store.building) setText(note, 'building…');
  else setText(note, `r${store.doc.revision}`);
}

// --------------------------------------------------------------------- timeline

function timelineFrames(name) {
  const parts = (store.doc && store.doc.parts) || {};
  const part = name ? parts[name] : null;
  return part && Array.isArray(part.timeline) ? part.timeline : [];
}

function timelineSteps() {
  const frames = timelineFrames(store.timelinePart);
  const steps = frames.map((frame) => ({
    kind: 'frame', index: frame.index, id: frame.id, type: frame.type,
  }));
  steps.push({ kind: 'final' });
  return steps;
}

function syncTimeline(doc) {
  const names = Object.keys(doc.parts || {}).filter((name) => timelineFrames(name).length);
  if (!store.timelinePart || !names.includes(store.timelinePart)) {
    store.timelinePart = names.includes(store.activePart) ? store.activePart : (names[0] || null);
  }
  const frames = timelineFrames(store.timelinePart);
  const signature = store.timelinePart ? `${store.timelinePart}:${frames.map((f) => f.index).join(',')}` : '';
  if (signature !== store.timelineSig) {
    store.timelineSig = signature;
    store.timelineStep = Math.max(0, timelineSteps().length - 1);
    clearFrameViews();
  }
}

function renderTimeline() {
  const doc = store.doc;
  const parts = (doc && doc.parts) || {};
  const names = Object.keys(parts).filter((name) => timelineFrames(name).length);
  const select = byId('timeline-part-select');
  const optionsKey = names.join('|');
  if (select.dataset.options !== optionsKey) {
    select.dataset.options = optionsKey;
    clearNode(select);
    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    }
  }
  if (store.timelinePart) select.value = store.timelinePart;
  select.disabled = names.length < 2;

  const steps = timelineSteps();
  if (store.timelineStep > steps.length - 1) store.timelineStep = Math.max(0, steps.length - 1);
  const scrub = byId('scrub');
  const ticks = byId('ticks');
  const play = byId('play');
  const note = byId('timeline-note');
  const frameLabel = byId('frame-label');
  clearNode(ticks);

  if (!store.timelinePart) {
    scrub.disabled = true;
    scrub.max = '0';
    scrub.value = '0';
    play.disabled = true;
    setText(frameLabel, doc && doc.revision != null ? `revision r${doc.revision}` : '-');
    let text = '';
    if (!doc) text = 'connecting';
    else if (doc.revision == null) text = store.building ? 'building the first revision' : 'waiting for the first build';
    else if (store.building) text = 'building — preview frames arrive as features finish';
    else text = 'no preview frames (cached build)';
    setText(note, text);
    return;
  }

  scrub.disabled = false;
  scrub.max = String(Math.max(0, steps.length - 1));
  scrub.value = String(store.timelineStep);
  play.disabled = steps.length < 2;
  setText(note, steps.length < 2 ? 'no preview frames yet' : '');

  steps.forEach((step, index) => {
    const tick = el('button', `tick${step.kind === 'final' ? ' final' : ''}${index === store.timelineStep ? ' on' : ''}`);
    tick.type = 'button';
    tick.dataset.step = String(index);
    tick.style.left = `${steps.length === 1 ? 100 : (index / (steps.length - 1)) * 100}%`;
    tick.title = step.kind === 'final'
      ? `revision r${doc.revision}`
      : `frame ${step.index} · ${step.id} · ${step.type}`;
    tick.setAttribute('aria-label', tick.title);
    tick.addEventListener('click', () => {
      if (store.timelinePlaying) stopPlay();
      setTimelineStep(index);
    });
    ticks.appendChild(tick);
  });

  const step = steps[store.timelineStep];
  if (!step || step.kind === 'final') {
    setText(frameLabel, doc && doc.revision != null ? `r${doc.revision}` : '-');
  } else {
    setText(frameLabel, `#${step.index} ${step.id} · ${step.type}`);
  }
}

function setTimelineStep(step) {
  const steps = timelineSteps();
  store.timelineStep = clamp(Math.round(step), 0, Math.max(0, steps.length - 1));
  if (steps[store.timelineStep]?.kind === 'frame' && (store.fea || store.feaRunning)) resetFea();
  renderTimeline();
  if (store.frameTimer) clearTimeout(store.frameTimer);
  store.frameTimer = setTimeout(() => {
    store.frameTimer = 0;
    showFrames();
  }, 50);
}

async function fetchFrame(part, index) {
  const key = `${part}:${index}:${store.frameEpoch}`;
  let pending = store.frames.get(key);
  if (!pending) {
    pending = (async () => {
      const response = await fetch(
        apiPath(`/api/frame?part=${encodeURIComponent(part)}&index=${encodeURIComponent(index)}`),
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error(`preview frame ${index} unavailable`);
      return parseMesh(await response.arrayBuffer());
    })();
    store.frames.set(key, pending);
    pending.catch(() => store.frames.delete(key));
  }
  return pending;
}

async function showFrames() {
  const steps = timelineSteps();
  const step = steps[store.timelineStep];
  const seq = ++store.frameSeq;
  if (!step || step.kind === 'final' || !store.timelinePart) {
    clearFrameViews();
    updateStatus();
    return;
  }
  const doc = store.doc;
  if (!doc) return;
  const jobs = [];
  for (const [name, part] of Object.entries(doc.parts || {})) {
    const frames = Array.isArray(part.timeline) ? part.timeline : [];
    if (!frames.length) continue;
    const candidate = frames.filter((frame) => frame.index <= step.index).pop() || frames[0];
    jobs.push({ name, part, frame: candidate });
  }
  const results = await Promise.allSettled(jobs.map((job) => fetchFrame(job.name, job.frame.index)));
  if (seq !== store.frameSeq) return;

  let failures = 0;
  const seen = new Set();
  results.forEach((result, index) => {
    const job = jobs[index];
    if (result.status === 'fulfilled') {
      applyFrameView(job.name, result.value, job.part, job.frame.index);
      seen.add(job.name);
    } else {
      failures += 1;
    }
  });
  for (const [name, partView] of [...store.frameGroups]) {
    if (!seen.has(name)) {
      disposeView(partView);
      store.frameGroups.delete(name);
    }
  }
  if (failures) setText(byId('timeline-note'), 'some preview frames are unavailable');
  applyVisibility();
  updateStatus();
}

function startPlayback(interval, auto) {
  const steps = timelineSteps();
  if (steps.length < 2) return;
  if (store.playTimer) clearTimeout(store.playTimer);
  store.timelinePlaying = true;
  store.autoPlaying = Boolean(auto);
  setPlayButton(true);
  if (store.timelineStep >= steps.length - 1) setTimelineStep(0);

  const tick = () => {
    if (!store.timelinePlaying) return;
    const total = timelineSteps().length;
    const next = store.timelineStep + 1;
    if (next >= total - 1) {
      setTimelineStep(total - 1);
      stopPlay();
      return;
    }
    setTimelineStep(next);
    store.playTimer = setTimeout(tick, interval);
  };
  store.playTimer = setTimeout(tick, interval);
}

function stopPlay() {
  store.timelinePlaying = false;
  store.autoPlaying = false;
  if (store.playTimer) {
    clearTimeout(store.playTimer);
    store.playTimer = 0;
  }
  setPlayButton(false);
}

function setPlayButton(playing) {
  const button = byId('play');
  if (!button) return;
  button.classList.toggle('on', playing);
  setText(button, playing ? 'pause' : 'play');
}

// --------------------------------------------------------------------- copy context

function featureLine(feature) {
  const ms = feature.ms != null ? ` ${fmt(feature.ms, 1)} ms` : '';
  const volume = feature.metrics && feature.metrics.volume != null
    ? `, ${fmt(feature.metrics.volume, 0)} mm3`
    : '';
  let line = `- ${feature.id} ${feature.type}${ms}${volume}`;
  if (feature.status && feature.status !== 'ok') line += ` [${feature.status}]`;
  const error = featureErrorText(feature);
  if (error && feature.status && feature.status !== 'ok') line += ` ${error}`;
  return line;
}

function buildContextMarkdown() {
  const doc = store.doc;
  const lines = [];
  if (!doc) {
    lines.push('# cad', '', `viewer: ${location.href}`);
    return lines.join('\n');
  }

  lines.push(`# ${doc.project || 'project'}${doc.revision != null ? ` r${doc.revision}` : ''}`);
  if (doc.entry) lines.push(`entry: ${doc.entry}`);

  const build = doc.build || {};
  const buildBits = [];
  if (build.duration_ms != null) buildBits.push(`${fmt(build.duration_ms, 0)} ms`);
  if (build.cached != null) buildBits.push(`cached ${build.cached ? 'yes' : 'no'}`);
  if (build.status) buildBits.push(`status ${build.status}`);
  if (buildBits.length) lines.push('', `build: ${buildBits.join(', ')}`);

  const parameters = doc.parameters || {};
  const overrides = doc.overrides || {};
  const parameterNames = Object.keys(parameters);
  if (parameterNames.length) {
    lines.push('', 'parameters:');
    for (const name of parameterNames) {
      const param = parameters[name] || {};
      const value = own(overrides, name) ? overrides[name] : (param.value != null ? param.value : param.default);
      const unit = param.unit ? ` ${param.unit}` : '';
      const mark = own(overrides, name) ? ' (override)' : '';
      lines.push(`- ${name} = ${formatScalar(value)}${unit}${mark}`);
    }
  }

  const parts = doc.parts || {};
  const partNames = Object.keys(parts);
  if (partNames.length) {
    lines.push('', 'parts:');
    for (const name of partNames) {
      const part = parts[name] || {};
      const summary = part.summary || {};
      const bits = [];
      if (summary.valid != null) bits.push(`valid ${summary.valid ? 'yes' : 'no'}`);
      if (summary.solids != null) bits.push(`${summary.solids} solid${summary.solids === 1 ? '' : 's'}`);
      if (summary.volume != null) bits.push(`${fmt(summary.volume, 0)} mm3`);
      if (summary.bbox && Array.isArray(summary.bbox.size)) {
        bits.push(`bbox ${summary.bbox.size.map((v) => fmt(v, 1)).join(' x ')} mm`);
      }
      if (summary.holes != null) bits.push(`${summary.holes} hole${summary.holes === 1 ? '' : 's'}`);
      if (summary.material) bits.push(`material ${summary.material}`);
      if (part.reference) bits.push('reference');
      lines.push(`- ${name}: ${bits.join(', ') || 'no summary'}`);
    }
  }

  const featureParts = partNames.filter((name) => {
    const features = parts[name] && parts[name].features;
    return Array.isArray(features) && features.length;
  });
  if (featureParts.length) {
    lines.push('', 'features:');
    for (const name of featureParts) {
      lines.push(`${name}:`);
      for (const feature of parts[name].features) lines.push(featureLine(feature));
    }
  }

  const selection = store.selection;
  if (selection) {
    const face = selection.info;
    const refs = face && Array.isArray(face.refs) ? face.refs : [];
    const label = refs.length ? refs.join(', ') : `face #${selection.faceId}`;
    const bits = [];
    if (face && face.area != null) bits.push(`area ${fmt(face.area, 2)} mm2`);
    if (face && Array.isArray(face.center)) bits.push(`centroid ${fmtPoint(face.center)} mm`);
    lines.push('', 'selection:', `- ${selection.part} ${label}${bits.length ? `, ${bits.join(', ')}` : ''}`);
  }

  const print = printDataForCurrent();
  if (print) {
    const bbox = print.geometry && Array.isArray(print.geometry.bbox_mm)
      ? print.geometry.bbox_mm.map(Number)
      : null;
    const material = print.material && print.material.name;
    const bits = [];
    if (print.mass_g != null) bits.push(`${fmt(print.mass_g, 2)} g`);
    if (bbox && bbox.length === 3) bits.push(`bbox ${bbox.map((value) => fmt(value, 1)).join(' x ')} mm`);
    if (print.bed) {
      const overflow = printBedOverflow(print);
      bits.push(print.bed.fits
        ? `fits ${printBedLabel(print)}`
        : `does not fit${overflow ? ` (${overflow} mm)` : ''}`);
    }
    lines.push('', `print${material ? ` (estimate for ${material})` : ''}: ${bits.join(', ')}`);
    const thin = print.thin || {};
    const thinBits = [String(thin.count != null ? thin.count : 0)];
    if (thin.min_mm != null) thinBits.push(`min ${fmt(thin.min_mm, 2)} mm`);
    if (thin.threshold_mm != null) thinBits.push(`threshold ${fmt(thin.threshold_mm, 2)} mm`);
    lines.push(`- thin walls: ${thinBits.join(', ')}`);
    const overhang = print.overhang || {};
    lines.push(`- overhang: ${fmt(overhang.area_mm2 || 0, 1)} mm2`);
    const codes = (print.warnings || []).map((warning) => warning.code).filter(Boolean);
    if (codes.length) lines.push(`- warnings: ${codes.join(', ')}`);
  }

  const warnings = doc.warnings || [];
  if (warnings.length) {
    lines.push('', 'warnings:');
    for (const warning of warnings) lines.push(`- ${warning.code || 'warning'}: ${warning.message || ''}`);
  }

  if (store.fea) {
    const meta = store.fea.meta || {};
    const report = meta.report || {};
    const study = meta.study || store.fea.study;
    const field = FIELD_LABELS[meta.field] || meta.field || store.fea.field;
    const bits = [];
    if (report.safety_factor != null) bits.push(`SF ${fmt(report.safety_factor, 2)}`);
    if (report.target != null) bits.push(`target ${fmt(report.target, 2)}`);
    lines.push('', `stress: ${study}${store.fea.quality ? ` (${store.fea.quality})` : ''}, ${field}${bits.length ? `, ${bits.join(', ')}` : ''}`);
    if (report.verdict) lines.push(`- ${report.verdict}`);
    const hotspots = Array.isArray(report.hotspots) ? report.hotspots : [];
    if (hotspots.length) {
      lines.push('- hotspots: ' + hotspots.map((hotspot) => (
        hotspot.von_mises_MPa != null
          ? `#${hotspot.id} ${fmt(hotspot.von_mises_MPa, 2)} MPa`
          : `#${hotspot.id}`
      )).join(', '));
    }
  }

  lines.push('', `viewer: ${location.href}`);
  return lines.join('\n');
}

async function copyContext() {
  const selection = store.selection;
  if (selection && selection.faceId != null && !selection.info) {
    try {
      const table = await faceTable(selection.part, store.revision);
      selection.info = table.get(selection.faceId) || null;
    } catch { /* copy with the face id only */ }
  }
  if (store.print.loading && store.print.promise) {
    try {
      await store.print.promise;
    } catch { /* the panel owns the error */ }
  }
  if (!printDataForCurrent() && store.doc && store.doc.revision != null && activePartName()) {
    try {
      await fetchPrint();
    } catch { /* copy without the print section */ }
  }
  const text = buildContextMarkdown();
  const copied = await writeClipboard(text);

  const button = byId('btn-copy');
  if (!copied) {
    pushNote('error', 'clipboard unavailable');
    return;
  }
  if (store.copyTimer) clearTimeout(store.copyTimer);
  button.classList.add('copied');
  setText(button, 'copied');
  store.copyTimer = setTimeout(() => {
    button.classList.remove('copied');
    setText(button, 'copy context');
  }, 1200);
}

async function writeClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to execCommand */ }
  return legacyCopy(text);
}

function showToast(text) {
  const node = byId('toast');
  if (!node) return;
  setText(node, text);
  node.classList.remove('hidden');
  if (store.toastTimer) clearTimeout(store.toastTimer);
  store.toastTimer = setTimeout(() => node.classList.add('hidden'), 1400);
}

function legacyCopy(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.top = '-2000px';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

// --------------------------------------------------------------------- status / banner

function displayedTriangles() {
  if (store.fea) return store.fea.view.triangles;
  let total = 0;
  if (store.frameGroups.size) {
    for (const partView of store.frameGroups.values()) total += partView.triangles;
    return total;
  }
  for (const partView of store.revisionGroups.values()) total += partView.triangles;
  return total;
}

function updateStatus() {
  const doc = store.doc;
  const build = (doc && doc.build) || {};
  setText(byId('status-project'), (doc && doc.project) || '-');
  setText(byId('status-revision'), doc && doc.revision != null ? `r${doc.revision}` : 'no revision');
  setText(byId('status-build'), build.duration_ms != null ? `${fmt(build.duration_ms, 0)} ms` : '-');
  let cache = '-';
  if (build.cached === true) cache = 'cached';
  else if (build.cached === false) cache = 'built';
  else if (build.status) cache = String(build.status);
  setText(byId('status-cache'), cache);
  setText(byId('status-tris'), `${fmtInt(displayedTriangles())} tris`);

  const pill = byId('status-connection');
  pill.className = `pill ${store.conn}`;
  setText(pill, store.conn === 'live' ? 'live' : store.conn === 'reconnecting' ? 'reconnecting' : 'paused');

  updateExportControls();
}

function paintProgress() {
  const strip = byId('progress');
  if (!store.building) {
    strip.classList.add('hidden');
    return;
  }
  strip.classList.remove('hidden');
  const progress = store.progress || {
    started: performance.now(), parts: {}, count: 0, frames: 0, current: null,
  };
  const seconds = ((performance.now() - progress.started) / 1000).toFixed(1);
  const current = progress.current;
  const where = current
    ? `${current.part} · ${current.id} · ${current.type} · ${fmt(current.ms, 1)} ms`
    : 'starting';
  const perPart = Object.entries(progress.parts || {})
    .map(([name, count]) => `${name} ${count}`)
    .join(' · ');
  const text = `building  ${where}  —  ${progress.count} feature${progress.count === 1 ? '' : 's'}`
    + `${perPart ? `  (${perPart})` : ''}  ·  ${seconds}s${progress.frames ? ` · ${progress.frames} frames` : ''}`;
  setText(byId('progress-text'), text);
}

function showBanner(error) {
  if (!error) {
    hideBanner();
    return;
  }
  const key = errorKey(error);
  if (key && key === store.dismissedError) return;
  store.bannerError = error;
  byId('banner').classList.remove('hidden');
  setText(byId('banner-code'), error.code || 'ERROR');
  setText(byId('banner-message'), error.message || '');
  const hint = byId('banner-hint');
  setText(hint, error.hint || '');
  hint.classList.toggle('hidden', !error.hint);
  const locationData = error.details && error.details.location;
  const locationNode = byId('banner-location');
  if (locationData) {
    const where = `${locationData.file || '?'}:${locationData.line != null ? locationData.line : '?'}`;
    setText(locationNode, `${where}: ${locationData.code || ''}`.trim());
    locationNode.classList.remove('hidden');
  } else {
    setText(locationNode, '');
    locationNode.classList.add('hidden');
  }
}

function hideBanner() {
  byId('banner').classList.add('hidden');
  store.bannerError = null;
}

function dismissBanner() {
  if (store.bannerError) store.dismissedError = errorKey(store.bannerError);
  hideBanner();
}

function revisionIsEmpty(doc) {
  if (!doc || doc.revision == null) return false;
  if (doc.empty === true) return true;
  return !Object.keys(doc.parts || {}).length;
}

async function probeOlderRevisions() {
  const doc = store.doc;
  if (!revisionIsEmpty(doc)) return;
  const revisions = (doc && doc.revisions) || [];
  const signature = `${doc.revision}|${doc.head}|${revisions.map((r) => r.n).join(',')}`;
  const probe = store.emptyProbe;
  if (probe.signature === signature || probe.running) return;
  probe.signature = signature;
  probe.running = true;
  const candidates = revisions
    .filter((revision) => revision.n !== doc.revision)
    .sort((a, b) => b.n - a.n)
    .slice(0, 5);
  const older = [];
  for (const candidate of candidates) {
    if (older.length >= 3) break;
    try {
      const response = await fetch(apiPath(`/api/state?rev=${encodeURIComponent(candidate.n)}`), { cache: 'no-store' });
      if (!response.ok) continue;
      const body = await response.json();
      const count = Object.keys((body && body.parts) || {}).length;
      if (body && body.empty !== true && count > 0) older.push({ n: candidate.n, parts: count });
    } catch { /* older revisions are a convenience, not a requirement */ }
  }
  probe.older = older;
  probe.running = false;
  if (revisionIsEmpty(store.doc)) renderEmptyModel();
}

function renderEmptyModel(message) {
  const node = byId('empty-model');
  const doc = store.doc;
  if (!node) return;
  const show = !message && revisionIsEmpty(doc);
  node.classList.toggle('hidden', !show);
  if (!show) return;

  const revision = doc && doc.revision;
  const revisionNode = byId('empty-revision');
  clearNode(revisionNode);
  revisionNode.append(
    el('span', 'dim', 'revision '),
    el('span', null, revision != null ? `r${revision}` : 'none'),
    el('span', 'dim', doc && doc.head != null ? `  ·  head r${doc.head}` : ''),
  );

  const notice = byId('empty-notice');
  notice.classList.toggle('hidden', !store.emptyNotice);
  setText(notice, store.emptyNotice || '');

  const error = byId('empty-error');
  error.classList.toggle('hidden', !store.emptyError);
  setText(error, store.emptyError || '');

  const browsing = isBrowsingHistory();
  setText(byId('empty-copy'), browsing ? 'This revision has no parts.' : 'Describe what you want to build in Design chat.');
  byId('empty-open-chat').classList.toggle('hidden', browsing);

  const older = byId('empty-older');
  clearNode(older);
  const probe = store.emptyProbe;
  const showOlder = probe.older.length > 0;
  older.classList.toggle('hidden', !showOlder);
  if (showOlder) {
    older.appendChild(el('span', 'dim', 'older revisions with parts:'));
    for (const item of probe.older) {
      const chip = el('button', 'chip', `view r${item.n}`);
      chip.type = 'button';
      chip.title = `${item.parts} part${item.parts === 1 ? '' : 's'} in r${item.n}`;
      chip.addEventListener('click', () => checkoutRevisionNumber(item.n));
      older.appendChild(chip);
    }
    older.appendChild(el('span', 'dim', 'or use the revision selector'));
  } else if (store.emptyProbe.running) {
    older.classList.remove('hidden');
    older.appendChild(el('span', 'dim', 'checking older revisions…'));
  }
  probeOlderRevisions();
}

async function createNewModel() {
  if (store.emptyBusy) return;
  const previous = store.doc && store.doc.head != null ? Number(store.doc.head) : null;
  store.emptyBusy = true;
  store.emptyError = '';
  renderEmptyModel();
  try {
    const body = await postJson(apiPath('/api/new-model'), { viewing: store.doc?.revision });
    const revision = body && body.revision != null ? body.revision : null;
    const suffix = revision != null ? `r${revision}` : 'a new revision';
    const backupName = body && body.backup
      ? String(body.backup).split('/').pop()
      : '';
    const backup = backupName ? ` · backup ${backupName}` : '';
    const back = previous != null && previous !== revision ? ` · the previous model is still r${previous}` : '';
    store.emptyNotice = `new model created — ${suffix}${back}${backup}`;
    pushNote('ok', `new model created — ${suffix}${back}`);
    showToast(previous != null && previous !== revision
      ? `new model ${suffix} — view r${previous} for the old one`
      : `new model ${suffix}`);
    await refreshState();
  } catch (error) {
    store.emptyError = `new model failed: ${error.message || error}`;
    pushNote('error', store.emptyError);
  } finally {
    store.emptyBusy = false;
    renderEmptyModel();
  }
}

function onNewModelClick() {
  if (isBrowsingHistory()) {
    showToast(`viewing r${store.viewRevision} — return to the current revision first`);
    pushNote('info', `history is read-only: return to the current revision to start a new model`);
    return;
  }
  const parts = store.doc && store.doc.parts ? Object.keys(store.doc.parts).length : 0;
  const head = store.doc && store.doc.head != null ? store.doc.head : null;
  if (parts > 0) {
    const where = head != null ? `r${head}` : 'the current revision';
    const ok = window.confirm(
      `Start a new model?\n\n` +
      `${where} stays in history (open it from the revision selector or the chips below).\n` +
      `model.py is replaced with an empty build() and a new revision is created.`);
    if (!ok) return;
  }
  void createNewModel();
}

function updateEmptyState(message) {
  const node = byId('empty');
  const doc = store.doc;
  const emptyModel = !message && revisionIsEmpty(doc);
  renderEmptyModel(message);
  if (emptyModel) {
    node.classList.add('hidden');
    return;
  }
  let text = message || '';
  if (!text && view.backend === 'none') text = '3D renderer unavailable — see the banner';
  if (!text) {
    if (!doc) text = 'connecting to the project';
    else if (doc.revision == null) text = store.building || doc.building ? 'building the first revision' : 'no revision yet — waiting for a build';
    else if (!store.revisionGroups.size) text = store.geometryError ? `mesh unavailable — ${store.geometryError}` : 'loading geometry';
  }
  node.classList.toggle('hidden', !text);
  if (text) setText(node, text);
}

// --------------------------------------------------------------------- state

function applyState(doc) {
  const previousRevision = store.revision;
  const revisionChanged = doc.revision !== previousRevision;
  store.doc = doc;
  store.revision = doc.revision;
  if (!revisionIsEmpty(doc)) {
    store.emptyNotice = '';
    store.emptyError = '';
  }

  if (revisionChanged) {
    store.selection = null;
    setChatSelection(null);
    store.hover = null;
    store.faceSeq += 1;
    store.hoverSeq += 1;
    store.feaSeq += 1;
    store.feaRunning = false;
    store.feaError = '';
    store.faceTables.clear();
    clearHighlight('sel');
    clearHighlight('hover');
    clearFrameViews();
    if (store.fea) clearFea();
  }

  const parts = doc.parts || {};
  store.activePart = store.activePart && parts[store.activePart]
    ? store.activePart
    : (Object.keys(parts)[0] || null);
  syncPrintContext();

  if (doc.building || store.eventBuilding) {
    store.building = true;
    if (!store.progress) {
      store.progress = { started: performance.now(), parts: {}, count: 0, frames: 0, current: null };
    }
  } else {
    store.building = false;
    store.progress = null;
  }

  if (doc.error) showBanner(doc.error);
  else {
    store.dismissedError = '';
    hideBanner();
  }

  syncTimeline(doc);
  updateChrome();
  renderParts();
  renderPartInfo();
  renderFeatureLedger();
  renderDiagnostics();
  renderActivity();
  renderTimeline();
  renderStress();
  renderSelection();
  renderPrintPanel();
  updateStatus();
  paintProgress();
  updateEmptyState();

  if (store.autoPlayPending) {
    store.autoPlayPending = false;
    if (timelineFrames(store.timelinePart).length > 1) {
      setTimelineStep(0);
      startPlayback(90, true);
    }
  }

  if (doc.revision == null) {
    if (store.revisionGroups.size) clearRevisionGeometry();
    store.loadedRevision = null;
    store.loadedPartsKey = '';
  } else {
    const key = Object.keys(parts).sort().join('|');
    if (store.loadedRevision !== doc.revision || store.loadedPartsKey !== key) {
      loadRevisionGeometry(doc);
    }
  }
}

async function refreshState() {
  const seq = ++store.stateSeq;
  try {
    const query = store.viewRevision != null ? `?revision=${encodeURIComponent(store.viewRevision)}` : '';
    const response = await fetch(apiPath(`/api/state${query}`), { cache: 'no-store' });
    if (!response.ok) throw new Error(`state HTTP ${response.status}`);
    const doc = await response.json();
    if (seq !== store.stateSeq) return;
    applyState(doc);
    postBoardState();
    renderHistoryBanner();
  } catch (error) {
    pushNote('error', `state request failed: ${error.message || error}`);
    updateEmptyState('cannot reach the cad server');
  }
}

function scheduleStateRefresh() {
  if (store.stateTimer) clearTimeout(store.stateTimer);
  store.stateTimer = setTimeout(() => {
    store.stateTimer = 0;
    refreshState();
  }, 150);
}

function pushNote(level, message) {
  const now = new Date();
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
  store.notes.unshift({ level, message, time });
  if (store.notes.length > 40) store.notes.length = 40;
  renderActivity();
}

// --------------------------------------------------------------------- events

function connectEvents() {
  const source = window.cadWorkspace.events;
  store.source = source;

  source.onopen = () => {
    store.conn = 'live';
    updateStatus();
    refreshState();
  };
  if (source.readyState === EventSource.OPEN) source.onopen();
  source.onerror = () => {
    store.conn = 'reconnecting';
    updateStatus();
  };

  source.addEventListener('build_start', (event) => handleBuildStart(readEvent(event)));
  source.addEventListener('feature', (event) => handleFeature(readEvent(event)));
  source.addEventListener('frame', (event) => handleFrame(readEvent(event)));
  source.addEventListener('build_ok', (event) => handleBuildOk(readEvent(event)));
  source.addEventListener('build_error', (event) => handleBuildError(readEvent(event)));
  source.addEventListener('state', () => scheduleStateRefresh());
  source.addEventListener('note', (event) => {
    const payload = readEvent(event);
    pushNote(payload.level || 'info', payload.message || '');
  });
}

function readEvent(event) {
  try {
    return JSON.parse(event.data || '{}');
  } catch {
    return {};
  }
}

function handleBuildStart(payload) {
  store.eventBuilding = true;
  store.building = true;
  store.progress = { started: performance.now(), parts: {}, count: 0, frames: 0, current: null };
  store.frameEpoch += 1;
  store.frameSeq += 1;
  store.feaSeq += 1;
  store.feaRunning = false;
  store.frames.clear();
  clearFrameViews();
  clearFea();
  clearPrintOverlay();
  syncPrintToggles();
  store.feaError = '';
  if (store.doc && store.doc.parts) {
    for (const part of Object.values(store.doc.parts)) part.timeline = [];
  }
  store.timelinePart = null;
  store.timelineSig = '';
  store.timelineStep = 0;
  renderStress();
  stopPlay();
  hideBanner();
  if (store.doc) store.doc.building = true;
  paintProgress();
  updateStatus();
  renderTimeline();
}

function handleFeature(payload) {
  if (!store.progress) {
    store.progress = { started: performance.now(), parts: {}, count: 0, frames: 0, current: null };
  }
  store.progress.current = payload;
  store.progress.count += 1;
  store.progress.parts[payload.part] = (store.progress.parts[payload.part] || 0) + 1;
  paintProgress();
}

function handleFrame(payload) {
  if (store.progress) store.progress.frames += 1;
  const doc = store.doc;
  const part = doc && doc.parts && doc.parts[payload.part];
  if (part) {
    if (!Array.isArray(part.timeline)) part.timeline = [];
    if (!part.timeline.some((frame) => frame.index === payload.index)) {
      part.timeline.push({ index: payload.index, id: payload.id, type: payload.type });
      part.timeline.sort((a, b) => a.index - b.index);
    }
    if (!store.timelinePart) store.timelinePart = payload.part;
    if (store.timelinePart === payload.part) {
      store.timelineStep = Math.max(0, timelineSteps().length - 1);
      renderTimeline();
      updateStatus();
    }
  }
  paintProgress();
}

function handleBuildOk(payload) {
  store.eventBuilding = false;
  store.building = false;
  store.progress = null;
  hideBanner();
  pushNote('ok', `build r${payload.revision} in ${fmt(payload.duration_ms, 0)} ms`);
  store.autoPlayPending = Number(payload.duration_ms) > 250;
  paintProgress();
  updateStatus();
  refreshState();
}

function handleBuildError(payload) {
  store.eventBuilding = false;
  store.building = false;
  store.progress = null;
  paintProgress();
  const error = (payload && payload.error) || { code: 'BUILD_FAILED', message: 'build failed' };
  showBanner(error);
  pushNote('error', `build failed: ${error.message || error.code}`);
  scheduleStateRefresh();
}

// --------------------------------------------------------------------- chat slot

const CHAT_OPEN_KEY = 'cad.chat.open';

function setChatOpen(open, options = {}) {
  store.chat.open = Boolean(open);
  const widget = byId('chat');
  widget.hidden = !store.chat.open;
  byId('workspace').classList.toggle('chat-open', store.chat.open);
  const button = byId('btn-chat');
  button.classList.toggle('on', store.chat.open);
  button.setAttribute('aria-pressed', String(store.chat.open));
  if (store.chat.open) {
    if (options.focus) {
      if (window.matchMedia(COMPACT_PANELS_QUERY).matches) {
        setPanelOpen('left', false);
        setPanelOpen('right', false);
      }
      byId('chat-root').querySelector('textarea')?.focus();
    }
  } else {
    if (options.focus) byId('btn-chat').focus();
  }
  if (options.persist !== false) {
    try {
      window.localStorage.setItem(CHAT_OPEN_KEY, store.chat.open ? '1' : '0');
    } catch { /* storage may be blocked */ }
  }
}

function postBoardState() {
  const doc = store.doc;
  if (doc) window.cadWorkspace.showBoard({ revision: doc.revision, head: doc.head, empty: revisionIsEmpty(doc),
    revisions: doc.revisions || [], parameters: doc.parameters || {}, overrides: doc.overrides || {} });
}

function setChatSelection(selection) {
  store.featureFaceIds = null;
  window.cadWorkspace.select(selection);
}

function postSelection(focus = false) {
  if (focus) window.cadWorkspace.dispatchEvent(new Event('focus-chat'));
}

function hidePartsFromChat() {
  clearSelection();
  const names = Object.keys((store.doc && store.doc.parts) || {});
  let changed = 0;
  for (const name of names) {
    if (store.visibility.get(name) !== false) {
      store.visibility.set(name, false);
      changed += 1;
    }
  }
  applyVisibility();
  renderParts();
  renderPrintPanel();
  updateStatus();
  pushNote('info', changed
    ? `chat hid ${changed} part${changed === 1 ? '' : 's'}`
    : 'chat hid all parts (already hidden)');
}

function postChatDraft() {
  if (!store.chat.pendingChatDraft) return;
  window.cadWorkspace.dispatchEvent(new CustomEvent('stress-draft', { detail: store.chat.pendingChatDraft }));
  store.chat.pendingChatDraft = '';
}

function initChatShell() {
  let open = true;
  try {
    if (window.localStorage.getItem(CHAT_OPEN_KEY) === '0') open = false;
  } catch { /* storage may be blocked */ }
  setChatOpen(open, { persist: false });

  byId('btn-chat').addEventListener('click', () => setChatOpen(!store.chat.open, { focus: true }));
  byId('chat-close').addEventListener('click', () => setChatOpen(false, { focus: true }));
  byId('chat-reload').addEventListener('click', () => window.cadWorkspace.dispatchEvent(new Event('reload-chat')));
  window.cadWorkspace.addEventListener('clear-selection', clearSelection);
  window.cadWorkspace.addEventListener('ask-selection', () => { setChatOpen(true, { focus: true }); postSelection(true); });
  window.cadWorkspace.addEventListener('replay-selection', () => jumpToFeature(store.chatSelection?.feature));
  window.cadWorkspace.addEventListener('hide-parts', hidePartsFromChat);
  window.cadWorkspace.addEventListener('refresh-board', scheduleStateRefresh);
  byId('chat-root').addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    if (store.chatSelection || store.selection || store.bedPreview.active) clearSelection();
    else setChatOpen(false, { focus: true });
  });
}

// --------------------------------------------------------------------- screenshot / actions

async function screenshot() {
  renderFrame();
  const blob = await captureCanvas();
  if (!blob) {
    pushNote('error', 'screenshot failed');
    return;
  }
  const doc = store.doc;
  const revision = doc && doc.revision != null ? doc.revision : 'head';
  const filename = `${sanitizeName(doc && doc.project)}-r${revision}.png`;
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 3000);
}

function canvasToBlob(canvas) {
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob || null), 'image/png');
    } catch {
      resolve(null);
    }
  });
}

function dataUrlToBlob(url) {
  const comma = url.indexOf(',');
  if (comma < 0) return null;
  const header = url.slice(0, comma);
  const data = url.slice(comma + 1);
  const mime = (/data:([^;]+)/.exec(header) || [])[1] || 'image/png';
  try {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: mime });
  } catch {
    return null;
  }
}

async function copyCanvasBlob(canvas) {
  const copy = document.createElement('canvas');
  copy.width = canvas.width;
  copy.height = canvas.height;
  const context = copy.getContext('2d');
  if (!context) return null;
  try {
    context.drawImage(canvas, 0, 0);
  } catch {
    return null;
  }
  return canvasToBlob(copy);
}

async function captureCanvas() {
  const canvas = view.renderer && view.renderer.domElement ? view.renderer.domElement : view.canvas;
  if (!canvas) return null;

  if (view.backend === 'webgl2') {
    try {
      const url = canvas.toDataURL('image/png');
      const blob = dataUrlToBlob(url);
      if (blob) return blob;
    } catch { /* fall through */ }
    const blob = await canvasToBlob(canvas);
    return blob || copyCanvasBlob(canvas);
  }

  // WebGPU: try the canvas APIs first, then a 2D copy of the presented frame.
  try {
    const blob = await canvasToBlob(canvas);
    if (blob && blob.size > 0) return blob;
  } catch { /* fall through */ }
  try {
    const url = canvas.toDataURL('image/png');
    const blob = dataUrlToBlob(url);
    if (blob) return blob;
  } catch { /* fall through */ }
  return copyCanvasBlob(canvas);
}

async function rebuild() {
  try {
    const response = await fetch(apiPath('/api/build'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true, viewing: store.doc?.revision }),
    });
    const body = await response.json();
    if (!response.ok || !body.ok) {
      throw new Error((body && body.error && body.error.message) || `HTTP ${response.status}`);
    }
    pushNote('info', 'rebuild queued');
  } catch (error) {
    showBanner({ code: 'BUILD_REQUEST_FAILED', message: String(error.message || error) });
  }
}

/**
 * History browsing is READ-ONLY: fetch that snapshot with ?revision=N and leave
 * HEAD and model.py alone. Editing from a snapshot would write a different
 * source than the one on screen, so mutating actions are gated while a past
 * revision is displayed (see `isBrowsingHistory`).
 */
async function checkoutRevisionNumber(value) {
  if (!Number.isFinite(value)) return;
  const head = store.doc && store.doc.head != null ? Number(store.doc.head) : null;
  store.viewRevision = head != null && value === head ? null : value;
  store.emptyNotice = '';
  store.emptyError = '';
  clearSelection();
  await refreshState();
}

function returnToCurrentRevision() {
  store.viewRevision = null;
  clearSelection();
  void refreshState();
}

function isBrowsingHistory() {
  return store.viewRevision != null;
}

/** The read-only banner: which snapshot is on screen and how to get back. */
function renderHistoryBanner() {
  const banner = byId('history-banner');
  const label = byId('history-text');
  if (!banner || !label) return;
  const browsing = isBrowsingHistory();
  banner.classList.toggle('hidden', !browsing);
  const rebuild = byId('btn-rebuild');
  const fresh = byId('btn-new-model');
  if (rebuild) rebuild.disabled = browsing;
  if (fresh) {
    fresh.disabled = browsing;
    fresh.classList.toggle('hidden', revisionIsEmpty(store.doc) && !store.doc?.error);
  }
  if (browsing) {
    const head = store.doc && store.doc.head != null ? store.doc.head : '?';
    setText(label, `viewing r${store.viewRevision} (read-only) · current is r${head}`);
    if (rebuild) rebuild.title = 'read-only while viewing history — return to current to rebuild';
    if (fresh) fresh.title = 'read-only while viewing history — return to current to start a new model';
  } else {
    if (rebuild) rebuild.title = 'rebuild the current script';
    if (fresh) fresh.title = 'Start a fresh script with an empty build(); older revisions stay in the selector';
  }
}

// --------------------------------------------------------------------- ui bindings

function setStressMode(active) {
  store.stressMode = active;
  byId('stress-panel').classList.toggle('hidden', !active);
  byId('design-inspector').classList.toggle('hidden', active);
  byId('right').setAttribute('aria-label', active ? 'Stress analysis' : 'Inspector');
  if (!active) {
    resetFea();
    store.feaError = '';
  }
  if (window.matchMedia(COMPACT_PANELS_QUERY).matches) setPanelOpen('left', false);
  setPanelOpen('right', true);
  renderStress();
  byId('right').scrollTop = 0;
  if (active) byId('stress-heading').focus();
}

function selectInspectorTool(tool) {
  const stress = tool === 'stress';
  if (store.stressMode !== stress) setStressMode(stress);
  byId('property-tools').classList.toggle('hidden', tool !== 'properties');
  byId('print-block').classList.toggle('hidden', tool !== 'print');
  for (const [id, name] of [['tool-properties', 'properties'], ['tool-print', 'print'], ['btn-stress', 'stress']]) {
    byId(id).setAttribute('aria-pressed', String(name === tool));
  }
  byId('right').scrollTop = 0;
}

function setPanelOpen(which, open) {
  byId('app').classList.toggle(`${which}-off`, !open);
  const button = byId(`btn-${which}`);
  button.classList.toggle('on', open);
  button.setAttribute('aria-pressed', String(open));

}

function panelOpen(which) {
  return !byId('app').classList.contains(`${which}-off`);
}

function togglePanel(which) {
  const open = !panelOpen(which);
  if (open && which !== 'timeline' && window.matchMedia(COMPACT_PANELS_QUERY).matches) {
    setPanelOpen(which === 'left' ? 'right' : 'left', false);
  }
  setPanelOpen(which, open);
}

function bindCompactPanels() {
  const query = window.matchMedia(COMPACT_PANELS_QUERY);
  let desktopPanels;
  const sync = ({ matches }) => {
    if (matches) {
      desktopPanels = ['left', 'right', 'timeline'].map((panel) => [panel, panelOpen(panel)]);
      setPanelOpen('left', false);
      setPanelOpen('right', false);
      setPanelOpen('timeline', false);
    } else if (desktopPanels) {
      for (const [panel, open] of desktopPanels) setPanelOpen(panel, open);
    }
  };
  query.addEventListener('change', sync);
  sync(query);
}

function setPrintCollapsed(collapsed) {
  const block = byId('print-block');
  const toggle = byId('print-toggle');
  if (block) block.classList.toggle('collapsed', collapsed);
  if (toggle) toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  store.print.collapsed = collapsed;
}

function openPrintSection() {
  selectInspectorTool('print');
  if (window.matchMedia(COMPACT_PANELS_QUERY).matches) setPanelOpen('left', false);
  setPanelOpen('right', true);
  setPrintCollapsed(false);
  try {
    window.localStorage.setItem(PRINT_COLLAPSED_KEY, '0');
  } catch { /* storage may be blocked */ }
  const block = byId('print-block');
  const toggle = byId('print-toggle');
  if (block) {
    block.classList.remove('flash-section');
    void block.offsetWidth;
    block.classList.add('flash-section');
    try {
      block.scrollIntoView({ block: 'start' });
    } catch { /* older engines: leave the panel where it is */ }
    setTimeout(() => block.classList.remove('flash-section'), 800);
  }
  if (toggle) toggle.focus();
}

function resetFea() {
  store.feaSeq += 1;
  store.feaRunning = false;
  if (store.fea) clearFea();
  renderStress();
  updateStatus();
}

function bindPointer() {
  const canvas = view.canvas;
  canvas.addEventListener('contextmenu', (event) => event.preventDefault());

  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    try { canvas.focus({ preventScroll: true }); } catch { /* older engines */ }
    store.pointerDown = { x: event.clientX, y: event.clientY, t: performance.now() };
  });

  canvas.addEventListener('pointerup', (event) => {
    if (event.button !== 0) return;
    const down = store.pointerDown;
    store.pointerDown = null;
    if (!down) return;
    const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
    if (moved > 5 || performance.now() - down.t > 700) return;
    const hit = raycastAt(event.clientX, event.clientY);
    if (hit && hit.faceId != null) selectFace(hit);
    else clearSelection();
  });

  canvas.addEventListener('pointermove', (event) => {
    if (event.buttons) return;
    store.hoverPoint = { x: event.clientX, y: event.clientY };
    if (store.hoverScheduled) return;
    store.hoverScheduled = true;
    store.hoverTimer = setTimeout(() => {
      store.hoverScheduled = false;
      const point = store.hoverPoint;
      if (!point) return;
      setHover(raycastAt(point.x, point.y));
    }, 150);
  });

  canvas.addEventListener('pointerleave', () => {
    store.hoverPoint = null;
    if (store.hover) {
      store.hover = null;
      store.hoverSeq += 1;
      renderSelection();
      updateHighlights();
    }
  });
}

function bindDockDivider() {
  const divider = byId('dock-divider');
  const main = byId('main');
  function resize(percent) {
    const value = Math.max(25, Math.min(70, Math.round(percent)));
    main.style.setProperty('--inspector-height', value + '%');
    divider.setAttribute('aria-valuenow', String(value));
  }
  divider.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    divider.setPointerCapture(event.pointerId);
  });
  divider.addEventListener('pointermove', event => {
    if (!divider.hasPointerCapture(event.pointerId)) return;
    const bounds = main.getBoundingClientRect();
    resize(100 * (event.clientY - bounds.top) / bounds.height);
  });
  divider.addEventListener('keydown', event => {
    const current = Number(divider.getAttribute('aria-valuenow'));
    const sizes = { ArrowUp: current - 5, ArrowDown: current + 5, Home: 25, End: 70 };
    if (!Object.hasOwn(sizes, event.key)) return;
    event.preventDefault();
    resize(sizes[event.key]);
  });
}

function bindUi() {
  bindDockDivider();
  bindCompactPanels();
  const treeButtons = byId('left').querySelectorAll('.dock-nav button');
  for (const tab of treeButtons) {
    tab.addEventListener('click', () => {
      for (const button of treeButtons) {
        const selected = button === tab;
        button.setAttribute('aria-pressed', String(selected));
        byId(button.getAttribute('aria-controls')).hidden = !selected;
      }
      byId('left').scrollTop = 0;
    });
  }
  byId('view-preset').addEventListener('change', (event) => {
    const select = event.currentTarget;
    applyView(select.value);
    select.value = '';
  });
  byId('btn-fit').addEventListener('click', () => fitView(sceneBox()));
  byId('btn-camera').addEventListener('click', toggleCamera);
  byId('btn-shot').addEventListener('click', screenshot);
  byId('btn-left').addEventListener('click', () => togglePanel('left'));
  byId('btn-right').addEventListener('click', () => togglePanel('right'));
  byId('tool-properties').addEventListener('click', () => selectInspectorTool('properties'));
  byId('tool-print').addEventListener('click', openPrintSection);
  byId('btn-stress').addEventListener('click', () => selectInspectorTool('stress'));
  byId('btn-timeline').addEventListener('click', () => togglePanel('timeline'));
  byId('btn-rebuild').addEventListener('click', rebuild);
  byId('btn-copy').addEventListener('click', copyContext);
  byId('banner-close').addEventListener('click', dismissBanner);
  window.cadWorkspace.addEventListener('view-revision', event => checkoutRevisionNumber(event.detail));
  window.cadWorkspace.addEventListener('change-parameter', event => queueParam(event.detail.name, event.detail.value));
  byId('btn-new-model').addEventListener('click', onNewModelClick);
  byId('history-return').addEventListener('click', returnToCurrentRevision);
  byId('empty-open-chat').addEventListener('click', () => {
    setChatOpen(true, { focus: true });
    postSelection(true);
  });
  byId('print-checks-link').addEventListener('click', openPrintSection);

  byId('export-stl').addEventListener('click', () => exportPart('stl'));
  byId('export-step').addEventListener('click', () => exportPart('step'));
  byId('export-3mf').addEventListener('click', () => exportPart('3mf'));

  const printBlock = byId('print-block');
  try {
    setPrintCollapsed(window.localStorage.getItem(PRINT_COLLAPSED_KEY) === '1');
  } catch {
    setPrintCollapsed(false);
  }
  byId('print-toggle').addEventListener('click', () => {
    const collapsed = !printBlock.classList.contains('collapsed');
    setPrintCollapsed(collapsed);
    try {
      window.localStorage.setItem(PRINT_COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch { /* storage may be blocked */ }
  });

  const thinToggle = byId('print-thin');
  const overToggle = byId('print-overhang');
  const togglePrintOverlay = (kind, checked) => {
    if (!checked) {
      if (store.print.overlayKind === kind) {
        clearPrintOverlay();
        syncPrintToggles();
      }
      return;
    }
    if (store.fea || store.feaRunning) resetFea();
    if (kind === 'thin' && overToggle) overToggle.checked = false;
    if (kind === 'overhang' && thinToggle) thinToggle.checked = false;
    applyPrintOverlay(kind);
  };
  thinToggle.addEventListener('change', () => togglePrintOverlay('thin', thinToggle.checked));
  overToggle.addEventListener('change', () => togglePrintOverlay('overhang', overToggle.checked));

  byId('timeline-part-select').addEventListener('change', (event) => {
    const name = event.target.value;
    if (!name) return;
    if (store.timelinePlaying) stopPlay();
    store.timelinePart = name;
    store.timelineSig = '';
    store.timelineStep = Math.max(0, timelineSteps().length - 1);
    clearFrameViews();
    renderTimeline();
    updateStatus();
  });

  byId('play').addEventListener('click', () => {
    if (store.timelinePlaying) stopPlay();
    else startPlayback(120, false);
  });

  const scrub = byId('scrub');
  scrub.addEventListener('pointerdown', () => {
    if (store.timelinePlaying) stopPlay();
  });
  scrub.addEventListener('input', (event) => {
    if (store.timelinePlaying) stopPlay();
    setTimelineStep(Number(event.target.value));
  });

  byId('section-on').addEventListener('change', (event) => {
    store.section.on = event.target.checked;
    byId('section-slider').disabled = !store.section.on;
    updateClipPlane();
  });
  byId('section-flip').addEventListener('change', (event) => {
    store.section.flip = event.target.checked;
    updateClipPlane();
  });
  for (const button of document.querySelectorAll('#right [data-axis]')) {
    button.addEventListener('click', () => setSectionAxis(button.dataset.axis));
  }
  byId('section-slider').addEventListener('input', (event) => {
    store.section.value = Number(event.target.value);
    renderSectionValue();
    updateClipPlane();
  });

  bindPointer();
  window.addEventListener('keydown', onKeyDown);
  document.addEventListener('pointerdown', () => {
    if (store.autoPlaying) stopPlay();
  }, true);
  window.addEventListener('beforeunload', () => {
    if (store.source) store.source.close();
  });
}

function focusInChat(target) {
  const chat = byId('chat');
  if (!chat || !target) return false;
  return chat.contains(target);
}

function focusInViewport() {
  const viewport = byId('viewport');
  const active = document.activeElement;
  if (!viewport || !active) return false;
  if (focusInChat(active)) return false;
  return active === view.canvas || viewport.contains(active);
}

function onKeyDown(event) {
  const target = event.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT'
    || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const numberIndex = NUMBER_KEYS.indexOf(event.key);
  if (numberIndex >= 0) {
    event.preventDefault();
    applyView(VIEW_KEYS[numberIndex]);
    return;
  }
  const key = event.key.toLowerCase();
  if (key === 'f') {
    event.preventDefault();
    fitView(sceneBox());
  } else if (key === 'p') {
    event.preventDefault();
    toggleCamera();
  } else if (key === 'c') {
    event.preventDefault();
    copyContext();
  } else if (key === 'escape') {
    // Precedence: the chat iframe owns Escape while it has focus; otherwise a
    // selection (or focus in the viewport) clears the selection and leaves the
    // chat alone.  Only a bare Escape closes the chat drawer.
    if (focusInChat(target) || focusInChat(document.activeElement)) return;
    if (window.matchMedia(COMPACT_PANELS_QUERY).matches) {
      const openPanel = ['left', 'right'].find(panelOpen);
      if (openPanel) {
        event.preventDefault();
        setPanelOpen(openPanel, false);
        byId('btn-' + openPanel).focus();
        return;
      }
    }
    event.preventDefault();
    if (store.chatSelection || store.selection || store.bedPreview.active) {
      clearSelection();
      return;
    }
    if (focusInViewport()) return;
    if (store.chat.open) {
      setChatOpen(false);
      byId('btn-chat').focus();
    }
  }
}

// --------------------------------------------------------------------- boot

async function boot() {
  let rendererReady = false;
  try {
    await initScene();
    rendererReady = true;
  } catch (error) {
    view.backend = 'none';
    view.gpuError = String(error && (error.message || error));
    console.error('cad: no GPU renderer', error);
    setGpuStatus();
    showBanner({
      code: 'RENDERER_FAILED',
      message: 'could not start a WebGL2 or WebGPU renderer',
      hint: view.gpuError,
    });
  }

  bindUi();
  initChatShell();
  connectEvents();
  refreshState();
  renderSelection();
  renderPartInfo();
  renderDiagnostics();
  renderActivity();
  renderPrintPanel();
  updateStatus();
  updateEmptyState();
  if (rendererReady) animate();
}

boot();

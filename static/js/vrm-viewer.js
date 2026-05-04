import * as THREE from "https://esm.sh/three@0.165.0";
import { GLTFLoader } from "https://esm.sh/three@0.165.0/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "https://esm.sh/three@0.165.0/examples/jsm/controls/OrbitControls.js";
import { VRMLoaderPlugin, VRMUtils } from "https://esm.sh/@pixiv/three-vrm@2.1.1?deps=three@0.165.0";

const THEME_VIEWER_BG = {
    "matrix-green": "#0d1411",
    "blue-onyx": "#0d1b2b",
    "crimson-steel": "#211214",
    "midnight-violet": "#18102a",
    "cyber-neon": "#0a1214",
    "acid-neon": "#111409",
    "arctic-mint": "#dde8e2",
    "peach-fuzz": "#f4e3dc",
    "lavender-dream": "#e5ddf4",
    "lemonade-pop": "#f1edbf",
};

function hexToRgb(color) {
    const normalized = String(color || "").trim();
    const match = normalized.match(/^#([0-9a-fA-F]{6})$/);
    if (!match) return null;
    const value = match[1];
    return {
        r: Number.parseInt(value.slice(0, 2), 16),
        g: Number.parseInt(value.slice(2, 4), 16),
        b: Number.parseInt(value.slice(4, 6), 16),
    };
}

function rgbToHex({ r, g, b }) {
    const toHex = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function getRelativeLuminance({ r, g, b }) {
    const normalize = (v) => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const rs = normalize(r);
    const gs = normalize(g);
    const bs = normalize(b);
    return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function shadeBrightBackground(hexColor) {
    const rgb = hexToRgb(hexColor);
    if (!rgb) return hexColor;

    // Keep dark themes unchanged; gently darken only bright backgrounds.
    const luminance = getRelativeLuminance(rgb);
    if (luminance < 0.52) return hexColor;

    const shaded = {
        r: rgb.r * 0.87,
        g: rgb.g * 0.87,
        b: rgb.b * 0.87,
    };
    return rgbToHex(shaded);
}

let VRMAnimationLoaderPlugin = null;
let createVRMAnimationClip = null;
let pendingCompanionActions = [];
let handleCompanionAction = null;
try {
    const mod = await import("https://esm.sh/@pixiv/three-vrm-animation@2.1.1?deps=three@0.165.0,@pixiv/three-vrm@2.1.1");
    VRMAnimationLoaderPlugin = mod?.VRMAnimationLoaderPlugin || null;
    createVRMAnimationClip = mod?.createVRMAnimationClip || null;
} catch (_error) {
    // VRMA plugin is optional. We still support best-effort clip loading fallback.
}

window.addEventListener("vrm-companion-action", (event) => {
    const requested = String(event?.detail?.action || "").trim();
    if (!requested) return;

    if (typeof handleCompanionAction === "function") {
        handleCompanionAction(requested);
        return;
    }

    pendingCompanionActions.push(requested);
    if (pendingCompanionActions.length > 20) {
        pendingCompanionActions = pendingCompanionActions.slice(-20);
    }
});

function parseConfig() {
    const node = document.getElementById("vrmConfig");
    if (!node) {
        return { selectedModel: "", actionNames: [] };
    }

    const selectedModel = String(node.dataset.selectedModel || "").trim();
    const defaultPose = String(node.dataset.defaultPose || "model_rest").trim();
    const flipFacing = String(node.dataset.flipFacing || "true").trim().toLowerCase() === "true";
    let actionNames = [];
    try {
        actionNames = JSON.parse(node.dataset.actionNames || "[]");
    } catch (_error) {
        actionNames = [];
    }

    return {
        selectedModel,
        defaultPose,
        flipFacing,
        actionNames: Array.isArray(actionNames) ? actionNames.filter(Boolean) : [],
    };
}

function getViewerBackgroundColor() {
    const root = document.documentElement;
    const theme = String(root?.dataset?.theme || "").trim().toLowerCase();
    if (theme && THEME_VIEWER_BG[theme]) {
        return shadeBrightBackground(THEME_VIEWER_BG[theme]);
    }

    const styles = window.getComputedStyle(root);
    const fallbackFromTheme = String(styles.getPropertyValue("--bg-alt") || "").trim();
    return shadeBrightBackground(fallbackFromTheme || "#0d1411");
}

async function initVrmViewer() {
    const portal = document.getElementById("vrmViewerPortal");
    const flipButton = document.getElementById("companionFlipBtn");
    if (!portal) {
        return;
    }

    const { selectedModel, defaultPose, flipFacing, actionNames } = parseConfig();
    if (!selectedModel) {
        portal.textContent = "No VRM model selected in Configuration.";
        return;
    }

    let isFacingFlipped = flipFacing;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(getViewerBackgroundColor());

    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
    camera.position.set(0, 1.4, 3.1);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(portal.clientWidth, portal.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.style.visibility = "hidden";
    portal.appendChild(renderer.domElement);

    const hemi = new THREE.HemisphereLight(0xd6f8e5, 0x203025, 1.0);
    scene.add(hemi);

    const key = new THREE.DirectionalLight(0xffffff, 1.05);
    key.position.set(1.5, 2.8, 2.2);
    scene.add(key);

    const fill = new THREE.DirectionalLight(0x7ec7a4, 0.35);
    fill.position.set(-2, 1.3, -1.5);
    scene.add(fill);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enablePan = false;
    controls.enableZoom = false;
    controls.enableRotate = true;
    controls.target.set(0, 1.2, 0);
    controls.minPolarAngle = Math.PI / 2;
    controls.maxPolarAngle = Math.PI / 2;
    controls.rotateSpeed = 0.45;
    controls.update();

    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    const vrmUrl = `/assets/vrm/${encodeURIComponent(selectedModel)}`;

    let vrm = null;
    let mixer = null;
    let activeAction = null;
    let activeClipName = "";
    const animationClipsByAction = new Map();
    const actionNameByAction = new WeakMap();
    let currentlyPlayingActionName = "";
    let lastRequestedActionAt = performance.now();
    let nextIdleResetAt = performance.now();

    const actionFileByName = new Map(actionNames.map((name) => [name.toLowerCase(), name]));
    const resetActionName = actionFileByName.get("showfullbody") || null;
    const resetActionKey = resetActionName ? resetActionName.toLowerCase() : "";

    function computeNextIdleResetAt(nowMs) {
        const minDelayMs = 12000;
        const maxDelayMs = 24000;
        return nowMs + minDelayMs + Math.random() * (maxDelayMs - minDelayMs);
    }

    function applyFacingRotation() {
        if (!vrm?.scene) return;
        vrm.scene.rotation.y = isFacingFlipped ? Math.PI : 0;
        if (flipButton) {
            flipButton.setAttribute("aria-pressed", isFacingFlipped ? "true" : "false");
        }
    }

    function applyDefaultPose(vrmModel, poseMode) {
        if (!vrmModel?.humanoid) {
            return;
        }

        const wantsNormalized = poseMode === "normalized_rest";

        // Prefer the selected reset path first, then fallback to whichever API exists.
        if (wantsNormalized && typeof vrmModel.humanoid.resetNormalizedPose === "function") {
            vrmModel.humanoid.resetNormalizedPose();
        } else if (!wantsNormalized && typeof vrmModel.humanoid.resetRawPose === "function") {
            vrmModel.humanoid.resetRawPose();
        } else if (typeof vrmModel.humanoid.resetRawPose === "function") {
            vrmModel.humanoid.resetRawPose();
        } else if (typeof vrmModel.humanoid.resetNormalizedPose === "function") {
            vrmModel.humanoid.resetNormalizedPose();
        }

        if (typeof vrmModel.humanoid.update === "function") {
            vrmModel.humanoid.update();
        }
    }

    function applyHandsDownRestPose(vrmModel) {
        // Prefer raw rest first to keep arms/hands down on most authored avatars.
        if (!vrmModel?.humanoid) {
            return;
        }
        if (typeof vrmModel.humanoid.resetRawPose === "function") {
            vrmModel.humanoid.resetRawPose();
        } else if (typeof vrmModel.humanoid.resetNormalizedPose === "function") {
            vrmModel.humanoid.resetNormalizedPose();
        }
        if (typeof vrmModel.humanoid.update === "function") {
            vrmModel.humanoid.update();
        }
    }

    function buildRetargetMap(vrmModel) {
        const humanoid = vrmModel?.humanoid;
        const map = new Map();
        if (!humanoid) {
            return map;
        }

        // Common VRM human bone names in source clips.
        const humanBones = [
            "hips", "spine", "chest", "upperChest", "neck", "head",
            "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
            "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
            "leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes",
            "rightUpperLeg", "rightLowerLeg", "rightFoot", "rightToes",
            "leftThumbMetacarpal", "leftThumbProximal", "leftThumbDistal",
            "leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal",
            "leftMiddleProximal", "leftMiddleIntermediate", "leftMiddleDistal",
            "leftRingProximal", "leftRingIntermediate", "leftRingDistal",
            "leftLittleProximal", "leftLittleIntermediate", "leftLittleDistal",
            "rightThumbMetacarpal", "rightThumbProximal", "rightThumbDistal",
            "rightIndexProximal", "rightIndexIntermediate", "rightIndexDistal",
            "rightMiddleProximal", "rightMiddleIntermediate", "rightMiddleDistal",
            "rightRingProximal", "rightRingIntermediate", "rightRingDistal",
            "rightLittleProximal", "rightLittleIntermediate", "rightLittleDistal"
        ];

        for (const boneName of humanBones) {
            const normalized = humanoid.getNormalizedBoneNode?.(boneName);
            const raw = humanoid.getRawBoneNode?.(boneName);
            const target = normalized || raw;
            if (!target?.name) continue;

            // Allow clips that use either camelCase or PascalCase prefixes.
            const pascal = boneName.charAt(0).toUpperCase() + boneName.slice(1);
            map.set(boneName.toLowerCase(), target.name);
            map.set(pascal.toLowerCase(), target.name);
        }

        return map;
    }

    function retargetRawClipToVrm(rawClip, vrmModel) {
        if (!rawClip || !Array.isArray(rawClip.tracks) || !vrmModel?.humanoid) {
            return rawClip;
        }

        const retargetMap = buildRetargetMap(vrmModel);
        if (!retargetMap.size) {
            return rawClip;
        }

        const retargetedTracks = [];
        for (const track of rawClip.tracks) {
            const sourceName = String(track?.name || "");
            const dotIdx = sourceName.indexOf(".");
            if (dotIdx < 1) {
                retargetedTracks.push(track);
                continue;
            }

            const sourceNode = sourceName.slice(0, dotIdx);
            const propertyPath = sourceName.slice(dotIdx);
            const targetNode = retargetMap.get(sourceNode.toLowerCase());

            if (!targetNode) {
                continue;
            }

            const cloned = track.clone();
            cloned.name = `${targetNode}${propertyPath}`;
            retargetedTracks.push(cloned);
        }

        if (!retargetedTracks.length) {
            return null;
        }

        return new THREE.AnimationClip(rawClip.name || "Clip", rawClip.duration, retargetedTracks);
    }

    function frameModel(rootObject) {
        // Ensure world matrices are current before reading bounds.
        rootObject.updateWorldMatrix(true, true);

        const bounds = new THREE.Box3().setFromObject(rootObject);
        if (bounds.isEmpty()) {
            return;
        }

        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        bounds.getSize(size);
        bounds.getCenter(center);

        const radius = Math.max(size.length() * 0.5, 0.35);

        // Place target around upper torso for a better default portrait framing.
        const targetY = center.y + size.y * 0.15;
        controls.target.set(center.x, targetY, center.z);

        const halfFov = THREE.MathUtils.degToRad(camera.fov * 0.5);
        const fitDistance = radius / Math.tan(Math.max(halfFov, 0.1));
        const distance = Math.max(fitDistance * 1.18, 1.2);

        // Keep camera on +Z axis so horizontal-only orbit works as intended.
        camera.position.set(center.x, targetY, center.z + distance);

        camera.near = Math.max(distance * 0.01, 0.01);
        camera.far = Math.max(distance * 12, 40);
        camera.updateProjectionMatrix();
        controls.update();
    }

    function resizeRenderer() {
        const width = portal.clientWidth || 1;
        const height = portal.clientHeight || 1;
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }

    async function loadAnimationClip(actionName) {
        const normalized = String(actionName || "").trim();
        if (!normalized) return null;
        if (animationClipsByAction.has(normalized)) {
            return animationClipsByAction.get(normalized);
        }

        const fileStem = actionFileByName.get(normalized.toLowerCase()) || normalized;
        const url = `/assets/vrma/${encodeURIComponent(fileStem)}.vrma`;

        const animationLoader = new GLTFLoader();
        if (VRMAnimationLoaderPlugin) {
            animationLoader.register((parser) => new VRMAnimationLoaderPlugin(parser));
        }

        const clip = await new Promise((resolve, reject) => {
            animationLoader.load(
                url,
                (gltf) => {
                    const vrmAnimation = Array.isArray(gltf?.userData?.vrmAnimations) ? gltf.userData.vrmAnimations[0] : null;
                    if (vrmAnimation && createVRMAnimationClip && vrm?.humanoid) {
                        try {
                            const vrmClip = createVRMAnimationClip(vrmAnimation, vrm);
                            resolve(vrmClip || null);
                            return;
                        } catch (_error) {
                            // Fall through to raw animation clips.
                        }
                    }

                    const rawClip = Array.isArray(gltf?.animations) ? gltf.animations[0] : null;
                    resolve(retargetRawClipToVrm(rawClip, vrm) || null);
                },
                undefined,
                reject
            );
        });

        animationClipsByAction.set(normalized, clip);
        return clip;
    }

    async function playAction(actionName) {
        if (!mixer) {
            return;
        }

        const name = String(actionName || "").trim();
        if (!name) {
            return;
        }

        try {
            const clip = await loadAnimationClip(name);
            if (!clip) {
                return;
            }

            if (activeAction) {
                activeAction.fadeOut(0.15);
            }

            const nextAction = mixer.clipAction(clip);
            nextAction.reset();
            nextAction.setLoop(THREE.LoopOnce, 1);
            nextAction.clampWhenFinished = true;
            nextAction.fadeIn(0.15);
            nextAction.play();

            actionNameByAction.set(nextAction, name);
            activeAction = nextAction;
            activeClipName = name;
            currentlyPlayingActionName = name;
            lastRequestedActionAt = performance.now();
        } catch (error) {
            console.warn("VRMA action load failed", name, error);
        }
    }

    function onCompanionAction(requestedAction) {
        const requested = String(requestedAction || "").trim();
        if (!requested || requested === activeClipName) {
            return;
        }
        lastRequestedActionAt = performance.now();
        nextIdleResetAt = computeNextIdleResetAt(lastRequestedActionAt);
        playAction(requested);
    }

    const gltf = await new Promise((resolve, reject) => {
        loader.load(vrmUrl, resolve, undefined, reject);
    });

    vrm = gltf.userData.vrm;
    if (!vrm) {
        portal.textContent = "Selected model did not load as VRM.";
        return;
    }

    VRMUtils.removeUnnecessaryVertices(vrm.scene);
    VRMUtils.removeUnnecessaryJoints(vrm.scene);
    vrm.scene.position.set(0, 0, 0);
    applyFacingRotation();
    scene.add(vrm.scene);
    applyHandsDownRestPose(vrm);
    vrm.update(0);
    frameModel(vrm.scene);

    if (flipButton) {
        flipButton.addEventListener("click", () => {
            isFacingFlipped = !isFacingFlipped;
            applyFacingRotation();
            vrm.update(0);
        });
    }

    mixer = new THREE.AnimationMixer(vrm.scene);
    mixer.addEventListener("finished", (event) => {
        const finishedName = String(actionNameByAction.get(event?.action) || "").trim().toLowerCase();
        const clipName = finishedName || String(activeClipName || "").trim().toLowerCase();
        if (finishedName) {
            currentlyPlayingActionName = "";
        }
        if (!resetActionName || !clipName || clipName === resetActionKey) {
            // Keep the resting baseline stable so new actions do not flash through a T-pose.
            applyHandsDownRestPose(vrm);
            vrm.update(0);
            return;
        }
        playAction(resetActionName);
    });
    handleCompanionAction = onCompanionAction;

    // Play reset animation once at startup so the avatar settles into a known state.
    if (resetActionName) {
        await playAction(resetActionName);
        nextIdleResetAt = computeNextIdleResetAt(performance.now());
    } else {
        applyDefaultPose(vrm, defaultPose);
        vrm.update(0);
    }

    renderer.domElement.style.visibility = "visible";

    if (pendingCompanionActions.length > 0) {
        const queued = pendingCompanionActions.slice();
        pendingCompanionActions = [];
        for (const action of queued) {
            handleCompanionAction(action);
        }
    }

    // Default pose is T-pose; actions are only played when explicitly requested.

    const clock = new THREE.Clock();
    function tick() {
        const delta = clock.getDelta();
        const nowMs = performance.now();

        if (
            resetActionName &&
            !currentlyPlayingActionName &&
            pendingCompanionActions.length === 0 &&
            nowMs >= nextIdleResetAt &&
            nowMs - lastRequestedActionAt >= 6000
        ) {
            playAction(resetActionName);
            nextIdleResetAt = computeNextIdleResetAt(nowMs);
        }

        vrm.update(delta);
        if (mixer) {
            mixer.update(delta);
        }
        controls.update();
        renderer.render(scene, camera);
        requestAnimationFrame(tick);
    }

    resizeRenderer();
    const resizeObserver = new ResizeObserver(() => {
        resizeRenderer();
    });
    resizeObserver.observe(portal);

    const themeObserver = new MutationObserver(() => {
        scene.background = new THREE.Color(getViewerBackgroundColor());
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    tick();
}

function startViewer() {
    initVrmViewer().catch((error) => {
        console.error("VRM viewer init failed", error);
        const portal = document.getElementById("vrmViewerPortal");
        if (portal) {
            portal.textContent = "Failed to initialize VRM companion viewer.";
        }
    });
}

// This module has top-level await; DOMContentLoaded may already be fired by the
// time execution reaches here, so initialize immediately when possible.
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startViewer, { once: true });
} else {
    startViewer();
}

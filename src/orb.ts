import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Clock,
  Color,
  Group,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  SphereGeometry,
  TorusGeometry,
  WebGLRenderer,
} from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

export interface Orb {
  setState(s: OrbState): void;
  setAnalyser(a: AnalyserNode | null): void;
  destroy(): void;
}

type Connection = {
  x1: number;
  y1: number;
  z1: number;
  x2: number;
  y2: number;
  z2: number;
};

type Electron = {
  sx: number;
  sy: number;
  sz: number;
  ex: number;
  ey: number;
  ez: number;
  t: number;
  speed: number;
};

export function createOrb(canvas: HTMLCanvasElement): Orb {
  let destroyed = false;

  const N = 1200;
  const MAX_LINES = 2600;
  const MAX_ELECTRONS = 40;

  const renderer = new WebGLRenderer({
    canvas,
    antialias: false,
    alpha: false,
    powerPreference: "low-power",
  });
  renderer.setPixelRatio(0.8);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x030814, 1);

  const scene = new Scene();

  const camera = new PerspectiveCamera(
    45,
    window.innerWidth / window.innerHeight,
    0.1,
    1200
  );
  camera.position.z = 82;

  const geo = new BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const phase = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.pow(Math.random(), 0.5) * 25;

    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);

    phase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new BufferAttribute(pos, 3));

  const mat = new PointsMaterial({
    color: 0xd7efff,
    size: 0.36,
    transparent: true,
    opacity: 0.62,
    sizeAttenuation: true,
    blending: AdditiveBlending,
    depthWrite: false,
  });

  const points = new Points(geo, mat);
  scene.add(points);

  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new BufferGeometry();
  lineGeo.setAttribute("position", new BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new LineBasicMaterial({
    color: 0xb8e4ff,
    transparent: true,
    opacity: 0,
    blending: AdditiveBlending,
    depthWrite: false,
  });

  const lines = new LineSegments(lineGeo, lineMat);
  scene.add(lines);

  const electronGeo = new BufferGeometry();
  const electronPos = new Float32Array(MAX_ELECTRONS * 3);
  electronGeo.setAttribute("position", new BufferAttribute(electronPos, 3));
  electronGeo.setDrawRange(0, 0);

  const electronMat = new PointsMaterial({
    color: 0xffffff,
    size: 0.5,
    transparent: true,
    opacity: 1,
    sizeAttenuation: true,
    blending: AdditiveBlending,
    depthWrite: false,
  });

  const electrons = new Points(electronGeo, electronMat);
  scene.add(electrons);

  const activeElectrons: Electron[] = [];
  let activeConnections: Connection[] = [];
  let electronSpawnRate = 0;
  let targetElectronRate = 0;
  let lastElectronSpawn = 0;

  const glowGroup = new Group();
  scene.add(glowGroup);

  const glowGeo = new SphereGeometry(6, 20, 20);

  const glowMatA = new MeshBasicMaterial({
    color: 0xd9f3ff,
    transparent: true,
    opacity: 0.05,
    blending: AdditiveBlending,
    depthWrite: false,
  });

  const glowMatB = new MeshBasicMaterial({
    color: 0x6dbfff,
    transparent: true,
    opacity: 0.034,
    blending: AdditiveBlending,
    depthWrite: false,
  });

  const glowA = new Mesh(glowGeo, glowMatA);
  const glowB = new Mesh(glowGeo, glowMatB);

  glowA.scale.setScalar(1.35);
  glowB.scale.setScalar(2.15);

  glowGroup.add(glowA);
  glowGroup.add(glowB);

  const accentGroup = new Group();
  scene.add(accentGroup);

  const torusOuter = new Mesh(
    new TorusGeometry(31, 0.14, 14, 180),
    new MeshBasicMaterial({
      color: 0x87d6ff,
      transparent: true,
      opacity: 0.08,
      blending: AdditiveBlending,
      depthWrite: false,
    }),
  );
  const torusInner = new Mesh(
    new TorusGeometry(24.8, 0.1, 14, 160),
    new MeshBasicMaterial({
      color: 0x9de9ff,
      transparent: true,
      opacity: 0.06,
      blending: AdditiveBlending,
      depthWrite: false,
    }),
  );
  const coreShell = new Mesh(
    new SphereGeometry(10.5, 18, 18),
    new MeshBasicMaterial({
      color: 0xdff5ff,
      transparent: true,
      opacity: 0.018,
      blending: AdditiveBlending,
      depthWrite: false,
    }),
  );

  torusOuter.rotation.x = Math.PI * 0.5;
  torusInner.rotation.x = Math.PI * 0.5;
  accentGroup.add(torusOuter);
  accentGroup.add(torusInner);
  accentGroup.add(coreShell);

  let state: OrbState = "idle";
  let lastState: OrbState = "idle";

  let targetRadius = 25;
  let currentRadius = 25;

  let targetSpeed = 0.3;
  let currentSpeed = 0.3;

  let targetBright = 0.6;
  let currentBright = 0.6;

  let targetSize = 0.34;
  let currentSize = 0.34;

  let targetLineAmount = 0.15;
  let lineAmount = 0.15;

  let targetLineDistance = 11.5;
  let currentLineDistance = 11.5;

  let targetRingOpacity = 0.08;
  let currentRingOpacity = 0.08;

  let targetCoreOpacity = 0.018;
  let currentCoreOpacity = 0.018;

  let spinX = 0;
  let spinY = 0;
  let spinZ = 0;
  let transitionEnergy = 0;

  let cloudZ = 0;
  let cloudZVel = 0;

  let analyser: AnalyserNode | null = null;
  let freqData = new Uint8Array(64);
  let bass = 0;
  let mid = 0;

  const clock = new Clock();

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  window.addEventListener("resize", onResize);

  function animate() {
    if (destroyed) return;
    requestAnimationFrame(animate);

    const t = clock.getElapsedTime();

    switch (state) {
      case "idle":
        targetRadius = 28;
        targetSpeed = 0.2;
        targetBright = 0.5;
        targetSize = 0.32;
        targetLineAmount = 0.15;
        targetLineDistance = 12.0;
        targetElectronRate = 0;
        targetRingOpacity = 0.075;
        targetCoreOpacity = 0.018;
        break;
      case "listening":
        targetRadius = 22;
        targetSpeed = 0.3;
        targetBright = 0.65;
        targetSize = 0.36;
        targetLineAmount = 0.4;
        targetLineDistance = 11.0;
        targetElectronRate = 0;
        targetRingOpacity = 0.16;
        targetCoreOpacity = 0.03;
        break;
      case "thinking":
        targetRadius = 16;
        targetSpeed = 0.5;
        targetBright = 0.7;
        targetSize = 0.28;
        targetLineAmount = 1.0;
        targetLineDistance = 13.0;
        targetElectronRate = 0.006;
        targetRingOpacity = 0.24;
        targetCoreOpacity = 0.04;
        break;
      case "speaking":
        targetRadius = 18;
        targetSpeed = 0.2;
        targetBright = 0.7;
        targetSize = 0.38;
        targetLineAmount = 0.8;
        targetLineDistance = 11.8;
        targetElectronRate = 0;
        targetRingOpacity = 0.2;
        targetCoreOpacity = 0.032;
        break;
    }

    currentRadius += (targetRadius - currentRadius) * 0.02;
    currentSpeed += (targetSpeed - currentSpeed) * 0.02;
    currentBright += (targetBright - currentBright) * 0.02;
    currentSize += (targetSize - currentSize) * 0.02;
    lineAmount += (targetLineAmount - lineAmount) * 0.02;
    currentLineDistance += (targetLineDistance - currentLineDistance) * 0.02;
    electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;
    currentRingOpacity += (targetRingOpacity - currentRingOpacity) * 0.05;
    currentCoreOpacity += (targetCoreOpacity - currentCoreOpacity) * 0.05;

    if (state !== lastState) {
      transitionEnergy = 1.0;
      lastState = state;
    }
    transitionEnergy *= 0.985;

    bass = 0;
    mid = 0;

    if (analyser) {
      analyser.getByteFrequencyData(freqData);
      let bSum = 0;
      let mSum = 0;
      for (let i = 0; i < 8; i++) bSum += freqData[i];
      for (let i = 8; i < 24; i++) mSum += freqData[i];
      bass = bSum / (8 * 255);
      mid = mSum / (16 * 255);
    }

    let zTarget = Math.sin(t * 0.12) * 8;
    if (state === "thinking") {
      zTarget = Math.sin(t * 0.3) * 15 + Math.sin(t * 0.9) * 6;
    } else if (state === "speaking") {
      zTarget = Math.sin(t * 0.15) * 6 - bass * 8;
    }

    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }

    points.rotation.x = spinX;
    points.rotation.y = spinY;
    points.rotation.z = spinZ;
    points.position.z = cloudZ;

    lines.rotation.copy(points.rotation);
    lines.position.z = cloudZ;

    electrons.rotation.copy(points.rotation);
    electrons.position.z = cloudZ;

    const p = geo.getAttribute("position") as BufferAttribute;
    const a = p.array as Float32Array;

    for (let i = 0; i < N; i++) {
      const i3 = i * 3;
      let x = a[i3];
      let y = a[i3 + 1];
      let z = a[i3 + 2];
      const px = phase[i];

      vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;

      vel[i3] += Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

      const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
      const pull = Math.max(0, dist - currentRadius) * 0.002 + 0.0003;
      vel[i3] -= (x / dist) * pull;
      vel[i3 + 1] -= (y / dist) * pull;
      vel[i3 + 2] -= (z / dist) * pull;

      if (bass > 0.05) {
        vel[i3] += (x / dist) * bass * 0.016;
        vel[i3 + 1] += (y / dist) * bass * 0.016;
        vel[i3 + 2] += (z / dist) * bass * 0.016;
      }

      if (state === "speaking" && mid > 0.1) {
        const pulse = Math.sin(t * 8 + px);
        vel[i3] += (x / dist) * mid * 0.01 * pulse;
        vel[i3 + 1] += (y / dist) * mid * 0.01 * pulse;
      }

      vel[i3] *= 0.992;
      vel[i3 + 1] *= 0.992;
      vel[i3 + 2] *= 0.992;

      a[i3] += vel[i3];
      a[i3 + 1] += vel[i3 + 1];
      a[i3 + 2] += vel[i3 + 2];
    }

    p.needsUpdate = true;

    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDist = currentLineDistance * (1 + bass * 0.4);
      const maxDistSq = maxDist * maxDist;
      const step = Math.max(1, Math.floor(N / 280));

      activeConnections = [];

      for (let i = 0; i < N && lineCount < MAX_LINES; i += step) {
        const i3 = i * 3;
        const x1 = a[i3];
        const y1 = a[i3 + 1];
        const z1 = a[i3 + 2];

        for (let j = i + step; j < N && lineCount < MAX_LINES; j += step) {
          const j3 = j * 3;
          const dx = a[j3] - x1;
          const dy = a[j3 + 1] - y1;
          const dz = a[j3 + 2] - z1;

          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1;
            la[idx + 1] = y1;
            la[idx + 2] = z1;
            la[idx + 3] = a[j3];
            la[idx + 4] = a[j3 + 1];
            la[idx + 5] = a[j3 + 2];

            if (activeConnections.length < 300) {
              activeConnections.push({
                x1,
                y1,
                z1,
                x2: a[j3],
                y2: a[j3 + 1],
                z2: a[j3 + 2],
              });
            }

            lineCount++;
          }
        }
      }

      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * 0.045;
    } else {
      lineGeo.setDrawRange(0, 0);
      activeConnections = [];
    }

    if (activeConnections.length > 0 && electronSpawnRate > 0.003) {
      if (activeElectrons.length < 3 && (t - lastElectronSpawn) > 1.1) {
        const conn = activeConnections[Math.floor(Math.random() * activeConnections.length)];
        activeElectrons.push({
          sx: conn.x1,
          sy: conn.y1,
          sz: conn.z1,
          ex: conn.x2,
          ey: conn.y2,
          ez: conn.z2,
          t: 0,
          speed: 0.003 + Math.random() * 0.002,
        });
        lastElectronSpawn = t;
      }
    }

    const ep = electronGeo.getAttribute("position") as BufferAttribute;
    const ea = ep.array as Float32Array;
    let aliveCount = 0;

    for (let e = activeElectrons.length - 1; e >= 0; e--) {
      const el = activeElectrons[e];
      el.t += el.speed;

      if (el.t >= 1) {
        activeElectrons.splice(e, 1);
        continue;
      }

      const ei = aliveCount * 3;
      ea[ei] = el.sx + (el.ex - el.sx) * el.t;
      ea[ei + 1] = el.sy + (el.ey - el.sy) * el.t;
      ea[ei + 2] = el.sz + (el.ez - el.sz) * el.t;
      aliveCount++;
    }

    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    mat.opacity = currentBright + bass * 0.05;
    mat.size = currentSize + bass * 0.04;

    if (state === "thinking") {
      mat.color.lerp(new Color(0x6ec4ff), 0.015);
      lineMat.color.lerp(new Color(0x6ec4ff), 0.015);
      glowMatA.color.lerp(new Color(0x8fdcff), 0.015);
      glowMatB.color.lerp(new Color(0x6ec4ff), 0.015);
      (torusOuter.material as MeshBasicMaterial).color.lerp(new Color(0x8ad4ff), 0.025);
      (torusInner.material as MeshBasicMaterial).color.lerp(new Color(0xb4f0ff), 0.025);
      (coreShell.material as MeshBasicMaterial).color.lerp(new Color(0x9fe1ff), 0.025);
    } else if (state === "speaking") {
      mat.color.lerp(new Color(0x5ab8f0), 0.015);
      lineMat.color.lerp(new Color(0x5ab8f0), 0.015);
      glowMatA.color.lerp(new Color(0x7cccf5), 0.015);
      glowMatB.color.lerp(new Color(0x5ab8f0), 0.015);
      (torusOuter.material as MeshBasicMaterial).color.lerp(new Color(0x74cfff), 0.025);
      (torusInner.material as MeshBasicMaterial).color.lerp(new Color(0xd9f7ff), 0.025);
      (coreShell.material as MeshBasicMaterial).color.lerp(new Color(0xb8e8ff), 0.025);
    } else if (state === "listening") {
      mat.color.lerp(new Color(0x83d8ff), 0.015);
      lineMat.color.lerp(new Color(0x83d8ff), 0.015);
      glowMatA.color.lerp(new Color(0xb2ecff), 0.015);
      glowMatB.color.lerp(new Color(0x71c6ff), 0.015);
      (torusOuter.material as MeshBasicMaterial).color.lerp(new Color(0xb7efff), 0.025);
      (torusInner.material as MeshBasicMaterial).color.lerp(new Color(0xe5fbff), 0.025);
      (coreShell.material as MeshBasicMaterial).color.lerp(new Color(0xd7f4ff), 0.025);
    } else {
      mat.color.lerp(new Color(0x4ca8e8), 0.015);
      lineMat.color.lerp(new Color(0x4ca8e8), 0.015);
      glowMatA.color.lerp(new Color(0x8fdcff), 0.015);
      glowMatB.color.lerp(new Color(0x5ab8f0), 0.015);
      (torusOuter.material as MeshBasicMaterial).color.lerp(new Color(0x7ac8ff), 0.025);
      (torusInner.material as MeshBasicMaterial).color.lerp(new Color(0xbbeaff), 0.025);
      (coreShell.material as MeshBasicMaterial).color.lerp(new Color(0xcceeff), 0.025);
    }

    const glowPulse =
      1 +
      Math.sin(t * 1.8) * 0.02 +
      bass * 0.12 +
      (state === "thinking" ? 0.05 : 0) +
      (state === "speaking" ? 0.03 : 0);

    glowA.scale.setScalar(1.35 * glowPulse);
    glowB.scale.setScalar(2.15 * glowPulse);

    glowGroup.rotation.copy(points.rotation);
    glowGroup.position.z = cloudZ * 0.25;

    const listeningWave = state === "listening" ? (0.06 + bass * 0.18 + mid * 0.08) : 0;
    const thinkingWave = state === "thinking" ? 0.1 + Math.sin(t * 2.6) * 0.03 : 0;
    const speakingWave = state === "speaking" ? 0.08 + bass * 0.22 + mid * 0.12 : 0;
    const accentWave = 1 + listeningWave + thinkingWave + speakingWave;

    const outerMaterial = torusOuter.material as MeshBasicMaterial;
    const innerMaterial = torusInner.material as MeshBasicMaterial;
    const coreMaterial = coreShell.material as MeshBasicMaterial;

    outerMaterial.opacity = currentRingOpacity * (0.7 + bass * 0.55);
    innerMaterial.opacity = currentRingOpacity * (0.48 + mid * 0.45);
    coreMaterial.opacity = currentCoreOpacity * (1 + bass * 0.4);

    torusOuter.rotation.z += 0.0016 + currentSpeed * 0.0026;
    torusOuter.rotation.y = Math.sin(t * 0.28) * 0.16 + spinY * 0.08;
    torusInner.rotation.z -= 0.0022 + currentSpeed * 0.0034;
    torusInner.rotation.x = Math.PI * 0.5 + Math.sin(t * 0.42) * 0.08;
    torusInner.rotation.y = Math.cos(t * 0.22) * 0.12;

    torusOuter.scale.setScalar(0.98 + accentWave * 0.08);
    torusInner.scale.setScalar(0.96 + accentWave * 0.06);
    coreShell.scale.setScalar(1 + accentWave * 0.09);

    accentGroup.rotation.copy(points.rotation);
    accentGroup.position.z = cloudZ * 0.18;

    camera.position.x = Math.sin(t * 0.02) * 5;
    camera.position.y = Math.cos(t * 0.03) * 3;
    camera.lookAt(0, 0, cloudZ * 0.2);

    renderer.render(scene, camera);
  }

  animate();

  return {
    setState(s: OrbState) {
      state = s;
    },
    setAnalyser(a: AnalyserNode | null) {
      analyser = a;
      if (a) freqData = new Uint8Array(a.frequencyBinCount);
    },
    destroy() {
      destroyed = true;
      window.removeEventListener("resize", onResize);
      renderer.dispose();
      geo.dispose();
      lineGeo.dispose();
      electronGeo.dispose();
      glowGeo.dispose();
      torusOuter.geometry.dispose();
      torusInner.geometry.dispose();
      coreShell.geometry.dispose();
      mat.dispose();
      lineMat.dispose();
      electronMat.dispose();
      glowMatA.dispose();
      glowMatB.dispose();
      (torusOuter.material as MeshBasicMaterial).dispose();
      (torusInner.material as MeshBasicMaterial).dispose();
      (coreShell.material as MeshBasicMaterial).dispose();
    },
  };
}

import React, { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Sky, Text, Grid } from '@react-three/drei';
import { useSimStore } from '../../stores/simStore';
import * as THREE from 'three';

// --- Military-style tactical drone model ---
function SimDrone() {
  const { telemetry, running, simState } = useSimStore();
  const droneRef = useRef<THREE.Group>(null);
  const propRefs = useRef<THREE.Mesh[]>([]);
  const exhaustRef = useRef<THREE.PointLight>(null);

  // Matte OD green / dark tactical colors
  const bodyMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#2d3a2e', metalness: 0.15, roughness: 0.85,
  }), []);
  const armMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#1a1f1a', metalness: 0.3, roughness: 0.7,
  }), []);
  const motorMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#0f0f0f', metalness: 0.9, roughness: 0.15,
  }), []);
  const propMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#1a1a1a', transparent: true, opacity: 0.4, side: THREE.DoubleSide,
  }), []);
  const accentMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#8b0000', metalness: 0.4, roughness: 0.5,
  }), []);
  const sensorMat = useMemo(() => new THREE.MeshStandardMaterial({
    color: '#111111', metalness: 0.7, roughness: 0.3,
  }), []);

  useFrame((_, delta) => {
    if (!droneRef.current || !running) return;

    droneRef.current.position.y = telemetry.alt_agl * 0.1;
    droneRef.current.rotation.x = THREE.MathUtils.degToRad(telemetry.pitch);
    droneRef.current.rotation.z = THREE.MathUtils.degToRad(-telemetry.roll);
    droneRef.current.rotation.y = THREE.MathUtils.degToRad(-telemetry.heading);

    // Spin props
    const isActive = simState !== 'IDLE' && simState !== 'LANDED';
    if (isActive) {
      propRefs.current.forEach((mesh, i) => {
        if (mesh) mesh.rotation.y += delta * (simState === 'TAKING_OFF' ? 60 : 40) * (i % 2 === 0 ? 1 : -1);
      });
    }

    // Thruster glow
    if (exhaustRef.current) {
      exhaustRef.current.intensity = isActive ? 0.3 + Math.sin(Date.now() * 0.01) * 0.1 : 0;
    }
  });

  const armPositions: [number, number, number][] = [
    [0.18, 0, 0.18],   // front-right
    [-0.18, 0, 0.18],  // front-left
    [-0.18, 0, -0.18], // rear-left
    [0.18, 0, -0.18],  // rear-right
  ];

  const isFront = (i: number) => i < 2;

  return (
    <group ref={droneRef}>
      {/* === Center body — angular/stealth shape === */}
      {/* Main chassis — hexagonal profile */}
      <mesh castShadow material={bodyMat}>
        <boxGeometry args={[0.14, 0.035, 0.10]} />
      </mesh>
      {/* Top plate — angled armor */}
      <mesh position={[0, 0.02, 0]} castShadow material={bodyMat}>
        <boxGeometry args={[0.12, 0.008, 0.08]} />
      </mesh>
      {/* Bottom skid plate */}
      <mesh position={[0, -0.022, 0]} castShadow material={armMat}>
        <boxGeometry args={[0.13, 0.005, 0.09]} />
      </mesh>

      {/* === Forward nose — angular wedge === */}
      <mesh position={[0.085, 0.005, 0]} rotation={[0, 0, -0.15]} castShadow material={bodyMat}>
        <boxGeometry args={[0.04, 0.025, 0.06]} />
      </mesh>
      {/* Nose tip */}
      <mesh position={[0.11, 0.005, 0]} rotation={[0, Math.PI / 4, 0]} castShadow material={accentMat}>
        <boxGeometry args={[0.015, 0.02, 0.015]} />
      </mesh>

      {/* === Camera/sensor pod (front underside) === */}
      <mesh position={[0.06, -0.03, 0]} castShadow material={sensorMat}>
        <sphereGeometry args={[0.018, 12, 8]} />
      </mesh>
      {/* Camera lens */}
      <mesh position={[0.078, -0.03, 0]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.008, 0.006, 0.005, 12]} />
        <meshStandardMaterial color="#001122" metalness={0.95} roughness={0.05} />
      </mesh>

      {/* === GPS module (top rear) === */}
      <mesh position={[-0.04, 0.032, 0]} castShadow material={sensorMat}>
        <cylinderGeometry args={[0.015, 0.015, 0.008, 8]} />
      </mesh>
      {/* GPS mast */}
      <mesh position={[-0.04, 0.04, 0]} material={armMat}>
        <cylinderGeometry args={[0.003, 0.003, 0.02, 6]} />
      </mesh>

      {/* === Antenna (rear) === */}
      <mesh position={[-0.07, 0.01, 0]} rotation={[0.3, 0, 0]} material={armMat}>
        <cylinderGeometry args={[0.002, 0.001, 0.05, 4]} />
      </mesh>

      {/* === Arms and motor pods === */}
      {armPositions.map((pos, i) => {
        const angle = Math.atan2(pos[2], pos[0]);
        const dist = Math.sqrt(pos[0] ** 2 + pos[2] ** 2);
        return (
          <group key={i}>
            {/* Arm — tapered tactical strut */}
            <mesh
              position={[pos[0] / 2, 0, pos[2] / 2]}
              rotation={[0, angle, 0]}
              castShadow
              material={armMat}
            >
              <boxGeometry args={[dist, 0.012, 0.022]} />
            </mesh>
            {/* Arm reinforcement rib */}
            <mesh
              position={[pos[0] / 2, 0.008, pos[2] / 2]}
              rotation={[0, angle, 0]}
              material={armMat}
            >
              <boxGeometry args={[dist * 0.6, 0.005, 0.012]} />
            </mesh>

            {/* Motor housing — cylindrical with heat fins */}
            <mesh position={pos} castShadow material={motorMat}>
              <cylinderGeometry args={[0.022, 0.025, 0.025, 12]} />
            </mesh>
            {/* Motor cap */}
            <mesh position={[pos[0], pos[1] + 0.014, pos[2]]} material={motorMat}>
              <cylinderGeometry args={[0.018, 0.022, 0.004, 12]} />
            </mesh>

            {/* Prop disc */}
            <mesh
              ref={(el) => { if (el) propRefs.current[i] = el; }}
              position={[pos[0], pos[1] + 0.018, pos[2]]}
              material={propMat}
            >
              <cylinderGeometry args={[0.08, 0.08, 0.003, 32]} />
            </mesh>

            {/* Front arms: red accent stripe */}
            {isFront(i) && (
              <mesh
                position={[pos[0] * 0.7, 0.01, pos[2] * 0.7]}
                rotation={[0, angle, 0]}
                material={accentMat}
              >
                <boxGeometry args={[0.04, 0.003, 0.024]} />
              </mesh>
            )}
          </group>
        );
      })}

      {/* === Landing gear — angular skids === */}
      {[-0.04, 0.04].map((z, i) => (
        <group key={`gear-${i}`}>
          <mesh position={[0, -0.035, z]} material={armMat} castShadow>
            <boxGeometry args={[0.16, 0.005, 0.008]} />
          </mesh>
          {/* Gear legs */}
          <mesh position={[0.05, -0.028, z]} rotation={[0, 0, 0.2]} material={armMat}>
            <boxGeometry args={[0.005, 0.015, 0.008]} />
          </mesh>
          <mesh position={[-0.05, -0.028, z]} rotation={[0, 0, -0.2]} material={armMat}>
            <boxGeometry args={[0.005, 0.015, 0.008]} />
          </mesh>
        </group>
      ))}

      {/* === Status LEDs === */}
      {/* Front status — green/red */}
      <mesh position={[0.07, -0.015, 0.02]}>
        <sphereGeometry args={[0.004, 8, 8]} />
        <meshStandardMaterial
          color={simState === 'FLYING' ? '#00ff44' : simState === 'EMERGENCY' ? '#ff0000' : '#222222'}
          emissive={simState === 'FLYING' ? '#00ff44' : simState === 'EMERGENCY' ? '#ff0000' : '#000000'}
          emissiveIntensity={3}
        />
      </mesh>
      <mesh position={[0.07, -0.015, -0.02]}>
        <sphereGeometry args={[0.004, 8, 8]} />
        <meshStandardMaterial
          color={simState === 'FLYING' ? '#00ff44' : simState === 'EMERGENCY' ? '#ff0000' : '#222222'}
          emissive={simState === 'FLYING' ? '#00ff44' : simState === 'EMERGENCY' ? '#ff0000' : '#000000'}
          emissiveIntensity={3}
        />
      </mesh>
      {/* Rear LEDs — amber */}
      <mesh position={[-0.07, -0.015, 0.02]}>
        <sphereGeometry args={[0.004, 8, 8]} />
        <meshStandardMaterial
          color={simState !== 'IDLE' ? '#ff8800' : '#222222'}
          emissive={simState !== 'IDLE' ? '#ff8800' : '#000000'}
          emissiveIntensity={2}
        />
      </mesh>
      <mesh position={[-0.07, -0.015, -0.02]}>
        <sphereGeometry args={[0.004, 8, 8]} />
        <meshStandardMaterial
          color={simState !== 'IDLE' ? '#ff8800' : '#222222'}
          emissive={simState !== 'IDLE' ? '#ff8800' : '#000000'}
          emissiveIntensity={2}
        />
      </mesh>

      {/* Thruster glow (underneath, visible during flight) */}
      <pointLight ref={exhaustRef} position={[0, -0.04, 0]} color="#ff4400" intensity={0} distance={0.5} />
    </group>
  );
}

function Ground() {
  return (
    <>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <planeGeometry args={[20, 20]} />
        <meshStandardMaterial color="#e8e8e8" />
      </mesh>
      <Grid
        position={[0, 0, 0]}
        args={[20, 20]}
        cellSize={0.5}
        cellThickness={0.5}
        cellColor="#cccccc"
        sectionSize={5}
        sectionThickness={1}
        sectionColor="#999999"
        fadeDistance={15}
        fadeStrength={1}
        infiniteGrid
      />
    </>
  );
}

function AltitudeMarker() {
  const { telemetry, running } = useSimStore();

  if (!running || telemetry.alt_agl < 0.1) return null;

  return (
    <group position={[0.4, 0, 0]}>
      {/* Altitude reference line */}
      <mesh position={[0, telemetry.alt_agl * 0.05, 0]}>
        <boxGeometry args={[0.005, Math.max(0.001, telemetry.alt_agl * 0.1), 0.005]} />
        <meshStandardMaterial color="#00ff88" transparent opacity={0.3} />
      </mesh>
      <Text
        position={[0.05, telemetry.alt_agl * 0.1, 0]}
        fontSize={0.04}
        color="#00ff88"
        anchorX="left"
      >
        {`${telemetry.alt_agl.toFixed(1)}m AGL`}
      </Text>
    </group>
  );
}

export function WorldRenderer() {
  const { running } = useSimStore();

  return (
    <div className="h-full relative">
      <Canvas
        camera={{ position: [0.8, 0.6, 0.8], fov: 50 }}
        shadows
        gl={{ antialias: true }}
      >
        <ambientLight intensity={0.6} />
        <directionalLight position={[5, 10, 5]} intensity={1.0} castShadow />
        <pointLight position={[0, 3, 0]} intensity={0.4} color="#88aaff" />
        <hemisphereLight args={['#b1e1ff', '#b97a20', 0.3]} />

        <SimDrone />
        <Ground />
        <AltitudeMarker />

        <OrbitControls
          target={[0, 0.3, 0]}
          minDistance={0.3}
          maxDistance={10}
        />

        <Sky sunPosition={[100, 20, 100]} />
        <fog attach="fog" args={['#dde4ee', 8, 25]} />
      </Canvas>

      {!running && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40">
          <div className="text-center">
            <div className="text-lg text-nexus-muted mb-2">Flight Simulator</div>
            <div className="text-sm text-nexus-muted">Press "Start Sim" to begin</div>
          </div>
        </div>
      )}
    </div>
  );
}

import React, { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Text, Environment } from '@react-three/drei';
import { useDroneStore } from '../../stores/droneStore';
import * as THREE from 'three';

function DroneModel() {
  const { currentConfig, selectComponent, selectedComponent } = useDroneStore();
  const groupRef = useRef<THREE.Group>(null);
  const motorRefs = useRef<THREE.Mesh[]>([]);

  const { frame, motors } = currentConfig;
  const armLength = frame.arm_length_mm / 1000; // Convert to meters for 3D scale
  const scale = 2; // Visual scale factor

  // Spin prop indicators
  useFrame((_, delta) => {
    motorRefs.current.forEach((mesh) => {
      if (mesh) mesh.rotation.y += delta * 30;
    });
  });

  // Motor positions based on layout
  const motorPositions = useMemo(() => {
    const count = motors.count;
    const positions: [number, number, number][] = [];

    if (frame.layout === 'X' || frame.layout === 'H') {
      for (let i = 0; i < count; i++) {
        const angle = ((2 * Math.PI * i) / count) + Math.PI / count;
        positions.push([
          Math.cos(angle) * armLength * scale,
          0.02,
          Math.sin(angle) * armLength * scale,
        ]);
      }
    } else if (frame.layout === '+') {
      const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
      for (let i = 0; i < Math.min(count, 4); i++) {
        positions.push([
          Math.cos(angles[i]) * armLength * scale,
          0.02,
          Math.sin(angles[i]) * armLength * scale,
        ]);
      }
    } else {
      for (let i = 0; i < count; i++) {
        const angle = (2 * Math.PI * i) / count;
        positions.push([
          Math.cos(angle) * armLength * scale,
          0.02,
          Math.sin(angle) * armLength * scale,
        ]);
      }
    }
    return positions;
  }, [motors.count, frame.layout, armLength]);

  const isSelected = (field: string) => selectedComponent === field;

  // Tactical green for selected, dark tones for unselected
  const selectedColor = '#4ade80';
  const frameColor = '#1a1a1a';
  const bodyColor = '#222222';
  const motorColor = '#333333';
  const propColor = '#444444';
  const batteryColor = '#1a2a1a';
  const cameraColor = '#2a1a2a';
  const gpsColor = '#1a2a1a';

  return (
    <group ref={groupRef}>
      {/* Center body / FC */}
      <mesh
        position={[0, 0, 0]}
        onClick={() => selectComponent('electronics.flight_controller')}
      >
        <boxGeometry args={[0.08 * scale, 0.02 * scale, 0.08 * scale]} />
        <meshStandardMaterial
          color={isSelected('electronics.flight_controller') ? selectedColor : bodyColor}
          emissive={isSelected('electronics.flight_controller') ? selectedColor : '#000000'}
          emissiveIntensity={isSelected('electronics.flight_controller') ? 0.5 : 0}
        />
      </mesh>

      {/* Arms */}
      {motorPositions.map((pos, i) => (
        <mesh
          key={`arm-${i}`}
          position={[pos[0] / 2, 0, pos[2] / 2]}
          rotation={[0, Math.atan2(pos[2], pos[0]), 0]}
        >
          <boxGeometry args={[armLength * scale, 0.008 * scale, 0.015 * scale]} />
          <meshStandardMaterial
            color={isSelected('frame') ? selectedColor : frameColor}
            emissive={isSelected('frame') ? selectedColor : '#000000'}
            emissiveIntensity={isSelected('frame') ? 0.4 : 0}
          />
        </mesh>
      ))}

      {/* Motors */}
      {motorPositions.map((pos, i) => (
        <group key={`motor-${i}`} position={pos}>
          {/* Motor body */}
          <mesh onClick={() => selectComponent('motors')}>
            <cylinderGeometry args={[0.015 * scale, 0.015 * scale, 0.015 * scale, 16]} />
            <meshStandardMaterial
              color={isSelected('motors') ? selectedColor : motorColor}
              emissive={isSelected('motors') ? selectedColor : '#000000'}
              emissiveIntensity={isSelected('motors') ? 0.4 : 0}
              metalness={0.8}
              roughness={0.2}
            />
          </mesh>

          {/* Prop disc */}
          <mesh
            ref={(el) => { if (el) motorRefs.current[i] = el; }}
            position={[0, 0.012 * scale, 0]}
            onClick={() => selectComponent('propellers')}
          >
            <cylinderGeometry args={[
              (currentConfig.propellers.size_inch * 0.0254 / 2) * scale,
              (currentConfig.propellers.size_inch * 0.0254 / 2) * scale,
              0.002 * scale,
              32
            ]} />
            <meshStandardMaterial
              color={isSelected('propellers') ? selectedColor : propColor}
              emissive={isSelected('propellers') ? selectedColor : '#000000'}
              emissiveIntensity={isSelected('propellers') ? 0.3 : 0}
              transparent
              opacity={0.4}
            />
          </mesh>

          {/* Motor label */}
          <Text
            position={[0, 0.04 * scale, 0]}
            fontSize={0.015 * scale}
            color="#4ade80"
            anchorX="center"
            anchorY="bottom"
            font={undefined}
          >
            {`M${i + 1}`}
          </Text>
        </group>
      ))}

      {/* Battery (underneath) */}
      <mesh
        position={[0, -0.015 * scale, 0]}
        onClick={() => selectComponent('battery')}
      >
        <boxGeometry args={[0.07 * scale, 0.025 * scale, 0.035 * scale]} />
        <meshStandardMaterial
          color={isSelected('battery') ? selectedColor : batteryColor}
          emissive={isSelected('battery') ? selectedColor : '#0a1a0a'}
          emissiveIntensity={isSelected('battery') ? 0.5 : 0.1}
        />
      </mesh>

      {/* Camera (front) */}
      <mesh
        position={[0.04 * scale, -0.005 * scale, 0]}
        rotation={[0, 0, -0.5]}
        onClick={() => selectComponent('sensors.camera')}
      >
        <boxGeometry args={[0.02 * scale, 0.02 * scale, 0.025 * scale]} />
        <meshStandardMaterial
          color={isSelected('sensors.camera') ? selectedColor : cameraColor}
          emissive={isSelected('sensors.camera') ? selectedColor : '#000000'}
          emissiveIntensity={isSelected('sensors.camera') ? 0.5 : 0}
        />
      </mesh>

      {/* GPS module (if present) */}
      {currentConfig.sensors.gps.present && (
        <mesh
          position={[0, 0.025 * scale, -0.03 * scale]}
          onClick={() => selectComponent('sensors.gps')}
        >
          <cylinderGeometry args={[0.015 * scale, 0.015 * scale, 0.005 * scale, 16]} />
          <meshStandardMaterial
            color={isSelected('sensors.gps') ? selectedColor : gpsColor}
            emissive={isSelected('sensors.gps') ? selectedColor : '#0a1a0a'}
            emissiveIntensity={isSelected('sensors.gps') ? 0.5 : 0.1}
          />
        </mesh>
      )}

      {/* Antenna (VTX/RX) */}
      <mesh position={[-0.04 * scale, 0.03 * scale, 0]}>
        <cylinderGeometry args={[0.002 * scale, 0.002 * scale, 0.04 * scale, 8]} />
        <meshStandardMaterial color="#333333" />
      </mesh>

      {/* Tactical ground grid — green lines on dark plane */}
      <gridHelper
        args={[1, 20, new THREE.Color('#4ade80').multiplyScalar(0.30), new THREE.Color('#4ade80').multiplyScalar(0.15)]}
        position={[0, -0.05, 0]}
      />
    </group>
  );
}

export function DroneCanvas3D() {
  return (
    <div className="flex-1 relative bg-[#0a0a0a]">
      <Canvas
        camera={{ position: [0.4, 0.3, 0.4], fov: 45 }}
        gl={{ antialias: true }}
      >
        {/* Dark scene lighting */}
        <color attach="background" args={['#0a0a0a']} />
        <ambientLight intensity={0.25} />
        <directionalLight position={[5, 5, 5]} intensity={0.5} />
        <pointLight position={[-3, 3, -3]} intensity={0.2} color="#4ade80" />
        <pointLight position={[3, 2, 3]} intensity={0.1} color="#4ade80" />
        <DroneModel />
        <OrbitControls
          enablePan={true}
          enableZoom={true}
          minDistance={0.2}
          maxDistance={2}
          target={[0, 0, 0]}
        />
        <Environment preset="night" />
      </Canvas>

      {/* Tactical HUD Overlay — top-left */}
      <div className="absolute top-3 left-3 font-mono">
        <div className="text-[10px] text-nexus-accent/70 tracking-[0.2em] uppercase">
          Equipment Preview // 3D Render
        </div>
        <div className="text-[9px] text-nexus-muted/50 tracking-widest mt-0.5">
          Click to select | Scroll zoom | Drag rotate
        </div>
      </div>

      {/* Corner decorations */}
      <div className="absolute top-0 left-0 w-4 h-4 border-l border-t border-nexus-accent/30" />
      <div className="absolute top-0 right-0 w-4 h-4 border-r border-t border-nexus-accent/30" />
      <div className="absolute bottom-0 left-0 w-4 h-4 border-l border-b border-nexus-accent/30" />
      <div className="absolute bottom-0 right-0 w-4 h-4 border-r border-b border-nexus-accent/30" />
    </div>
  );
}

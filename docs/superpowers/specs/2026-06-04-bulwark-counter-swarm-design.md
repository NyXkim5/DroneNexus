# BULWARK — Counter-Swarm Defense Engine (Design Spec)

Date: 2026-06-04
Status: Approved for planning
Built on: OVERWATCH (DroneNexus) ISR coordination platform

---

## 1. Vision

BULWARK is the autonomous operating system for counter-swarm defense.

The next war is won in software. A 500 dollar drone should never beat a defended
site, yet today it does. Defense is a junk drawer of radars, jammers, cameras, and
humans that do not talk to each other. That works against one hobby drone. It fails
against a thousand coordinated ones.

The winner is not a better missile. It is the system that turns every sensor and
every effector on a site into one autonomous, real-time, self-coordinating organism.
Cloudflare, not Raytheon.

BULWARK is that system. One platform. Any sensor in, any effector out, machine-speed
decisions in between.

### The economic moat

Every dollar of advantage sits with the attacker. A Patriot costs three million. An
FPV drone costs five hundred. BULWARK flips the cost-exchange ratio. It neutralizes
many threats per defender and uses cheap non-kinetic effects first. The goal is to
make defense cheaper per kill than the attack. That number stays live on every screen.
It is the whole company in one metric.

---

## 2. Three pillars

Each pillar maps to a proven defense-tech category.

### Pillar 1: The Mesh (our Lattice)

A hardware-agnostic fusion fabric. Plug in any sensor (radar, EO/IR, RF, acoustic)
and any effector (interceptor, jammer, directed energy, net). BULWARK fuses them into
a single live track picture in milliseconds. Open by design. It does not sell the
hardware. It commands all of it. The site becomes one weapon.

### Pillar 2: The Ontology (our Gotham)

Counter-swarm modeled as a living data ontology. Core objects: Threat, Swarm, Track,
Defender, Engagement, Site. Every object carries identity, state, relationships, and
history. Operators do not read sensor feeds. They reason over a world model. This is
also the audit and after-action layer. Every decision is explainable and replayable.
OVERWATCH already speaks a Gotham-style ontology (Asset, Taskforce, Mission,
Observation). BULWARK extends it from passive ISR to active defense.

### Pillar 3: The Autonomy (our Hivemind)

The brain. At swarm scale humans are too slow. BULWARK classifies swarm intent
(saturation, decoys, waves), prioritizes thousands of threats, and allocates finite
defenders optimally. It runs autonomously, comms-denied, and jam-resistant. Human on
the loop, not in the loop. As radio jamming goes obsolete, the fight moves to the
autonomy stack. That is where BULWARK lives.

---

## 3. Strategy

### The wedge

We do not start by building interceptors. We start as the software layer that makes
existing defenses work as one. We prove it in high-fidelity wargame against
1000-drone attacks. Then we deploy the same engine against live sensors. Same brain,
simulated or real. Land as the operating system. Expand to own the kill chain.

### Why us, why now

- A real-time multi-asset C2 and ontology engine already exists in OVERWATCH. We are
  re-pointing a working system, not starting from a deck.
- Swarms are here. Jamming is dying. No one owns the autonomous fusion layer yet.

---

## 4. Architecture

The central design rule makes the whole thing possible: one engine, two sources, one
picture. The decision engine consumes abstract detections. It never knows whether a
detection came from a simulator or a real radar. This single boundary delivers both
the deployable system and the wargaming tool without forking the codebase.

```
  +- SimSensorSource --+                              wargaming
  |  (drone-sim feeds   |                                 ^
  |   noisy detections) |                                 |
  +---------------------+     +------------------+    +----+-----+
  |   SensorSource API  |---->|  FUSION ENGINE   |--->| Track DB |
  +---------------------+     |  multi-sensor -> |    +----+-----+
  |  RealSensorSource   |     |  unified tracks  |         |
  |  (mavlink/msp/usb)  |     +------------------+         v
  +- deployable --------+              |             +--------------+
                                       v             | THREAT       |
                              +------------------+   | CLASSIFIER   |
                              | ALLOCATION ENGINE|<--| + PRIORITIZER|
                              | defenders->threats|  | (swarm-aware)|
                              +--------+---------+   +--------------+
                                       |
                                       v
                              +------------------+
                              |  HUD (OVERWATCH) |  single real-time picture
                              +------------------+
```

### Reuse of existing OVERWATCH assets

- Deployable plumbing already exists: `backend/mavlink/`, `backend/msp/`,
  `backend/usb/`, and `backend/telemetry/` (collector, aggregator, replay).
- Wargaming plumbing already exists: `backend/simulation/mock_drone.py` and the
  `drone-sim/` Electron app with real physics, a noisy sensor model
  (`lib/sensors.ts`), and a sim loop.
- Swarm math already exists but points at friendly drones: `backend/swarm/`
  (coordinator, formations, collision, geofence, alerts). Counter-swarm inverts it
  to detect and defeat hostile swarms.

---

## 5. Modules

Each module has one purpose and a defined interface so it can be built and tested in
isolation.

1. `backend/sensors/` (new). The `SensorSource` interface plus two adapters.
   `SimSensorSource` subscribes to drone-sim output. `RealSensorSource` wraps the
   existing telemetry collector. Both emit `Detection` events with position,
   velocity, confidence, sensor of origin, and noise.

2. `backend/fusion/` (new). Multi-sensor track fusion. Associates detections across
   sensors and time into stable `Track` objects using nearest-neighbor association
   plus Kalman-style smoothing. Designed for 1000-plus simultaneous contacts. This is
   the hard core.

3. `backend/threat/` (new, inverts `swarm/`). Classifies a swarm, not just a drone.
   Detects formation, coordinated intent, and attack vector. Reuses the math in
   `swarm/formations.py` and `swarm/collision.py`, pointed at hostiles. Outputs threat
   scores and a priority ranking.

4. `backend/defense/` (new). Defender allocation. Assigns finite defenders
   (interceptors, jammers), each with capacity, range, reload, and kill probability,
   to many threats. Pluggable allocators, starting greedy and moving to auction or
   Hungarian assignment. This is the optimization humans cannot do at swarm scale.

5. `backend/attacker/` (new). Hostile swarm generator built on
   `simulation/mock_drone.py`. Configurable size from 10 to 1000. Behaviors include
   saturation, waves, decoys, and terrain-following, with coordination. This drives
   the whole pipeline.

6. `backend/wargame/` (new). Scenario definitions, run orchestration, and metrics
   (leakers, intercept rate, cost-exchange ratio in dollars per kill). Replay reuses
   `telemetry/replay.py`. This layer generates insight and powers the demo.

### HUD

Re-point `src/hud/` from friendly-asset monitoring to a threat picture. Hostile tracks
in red. Defender assignments as lines. Engagement outcomes. The cost-exchange counter
front and center, showing attacker cost against defender cost in real time.

---

## 6. Data flow

1. A `SensorSource` emits `Detection` events at sensor rate.
2. The fusion engine associates detections into `Track` objects and maintains the
   Track DB.
3. The threat classifier groups tracks into `Swarm` objects, infers intent, and scores
   each threat.
4. The prioritizer ranks threats by score, time-to-impact, and value at risk.
5. The allocation engine assigns `Defender` objects to threats and emits `Engagement`
   objects.
6. Engagements resolve (hit, miss, leak) and feed back into the world model.
7. The HUD renders the full picture and the cost-exchange metric.

All objects live in the ontology and are recorded for replay and after-action review.

---

## 7. Error handling and resilience

- Sensor dropout: fusion must coast a track on last-known state with growing
  uncertainty, then expire it after a timeout.
- Conflicting detections: confidence-weighted association, never a hard crash on
  ambiguous data.
- Allocator overload: when threats exceed defender capacity, the system must degrade
  gracefully and surface predicted leakers rather than fail.
- Comms-denied operation: the autonomy path must reach a defensible allocation with no
  operator input.

---

## 8. Testing

- Unit tests per module, co-located with source.
- Fusion: synthetic detection streams with known ground truth, assert track accuracy
  and identity stability under noise and dropout.
- Threat: labeled swarm scenarios, assert correct intent classification.
- Defense: assignment problems with known optimal solutions, assert allocation quality
  and runtime at scale.
- Wargame: end-to-end scenarios, assert metric outputs are stable and reproducible.
- Stress: extend the existing `backend/tests/test_stress.py` to 1000-plus contacts.

---

## 9. Phasing

The full platform is several specs. Build one spine at a time.

### Spec 1: the spine that proves the thesis (this build)

`attacker` to `SimSensorSource` to `fusion` to `threat` to `defense` (greedy
allocator) to HUD, with a basic `wargame` runner. This is a complete end-to-end demo
against a simulated swarm.

### Spec 2: the deployable path

`RealSensorSource` against live `mavlink`/`msp`/`usb` feeds. Same engine, real data.

### Spec 3: advanced autonomy and analytics

Auction or Hungarian allocators, non-kinetic effect modeling, deeper wargame analytics,
and after-action reporting.

---

## 10. Open questions for later specs

- Which allocation algorithm wins at 1000-plus threats under a hard latency budget.
- Whether the autonomy core runs in Python or needs a faster runtime for real-time
  guarantees.
- Effect modeling fidelity for non-kinetic defenses (aerosols, entangling streamers).

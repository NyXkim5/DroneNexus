# BULWARK Architecture

Counter-swarm defense engine, built on the OVERWATCH platform.

This document describes what the code does, not what it aspires to do. Where a
part is a simplification or a known gap, it says so.

## 1. Overview and thesis

A 500 dollar drone should not beat a defended site. Today it can. The defense is
a set of radars, jammers, cameras, and operators that do not talk to each other.
That works against one hobby drone. It fails against a thousand coordinated ones.

BULWARK treats counter-swarm defense as a real-time distributed systems problem,
not a missile problem. The site is full of sensors and effectors that already
exist. The missing layer is the software that fuses every sensor into one track
picture, reasons over it at machine speed, and commands every effector as one
weapon. This is the Cloudflare-not-Raytheon framing. Do not sell the hardware.
Command all of it.

The metric that decides the fight is the cost-exchange ratio (CER): defender
dollars spent per attacker dollar of airframe destroyed. Below 1.0 means defense
is cheaper than the attack. The whole system exists to drive that number below
one and keep it there against a hardened, reactive raid. In this engine the CER
is emergent. It falls out of which effectors killed which drones, not a number
set by hand. See section 5.

The current build is a high-fidelity wargame plus the deployable seam. It runs a
1000-drone saturation attack end to end and surfaces the CER live. The same
engine accepts real sensor data through one interface with no other change.

## 2. One engine, two sources

The central design rule is one engine, two sources, one picture. The decision
engine consumes abstract `Detection` events. It never knows whether a detection
came from a simulator or a real radar. This single boundary delivers both the
wargame and the deployable system from one codebase.

The contract is `SensorSource` (`backend/sensors/base.py`). It is an abstract
async source with `start`, `stop`, a `stream()` async generator, and a
`sample_once()` pull for a caller that owns the clock. The fusion engine pulls
`Detection` objects and does not care where they came from.

Two adapters implement the contract:

- `SimSensorSource` (`backend/sensors/sim_source.py`). Samples ground truth from
  the attacker simulation and emits noisy detections. It models radar, EO/IR, and
  RF/passive phenomenology: RCS and range dependent detection probability with a
  radar-equation SNR, range-grown anisotropic measurement noise, sensor-type
  differentiation, false alarms drawn from a Poisson count, and SNR-based
  confidence. It does not import attacker internals. It reads a truth callable, so
  the attacker and sensor layers stay decoupled.

- `RealSensorSource` (`backend/sensors/real_source.py`). Feeds the same engine
  from real-world data. Two modes exist since no hardware is wired in. `from_jsonl`
  replays a recorded JSONL capture of contacts in timestamp order. `from_telemetry`
  maps a live `telemetry.collector.DroneState` feed into detections, converting
  geodetic lat/lon/alt into the local ENU site frame. It exposes the exact
  `sample_once()` surface, so the runner drives it with no change.

The runner injects either source. The default is the simulator. A deployment
passes a `RealSensorSource` to the same constructor.

## 3. The tick pipeline

`WargameRunner` (`backend/wargame/runner.py`) owns the clock and runs the full
pipeline once per tick. `run()` is an async generator of `Frame`, so the CLI and
the websocket consume it the same way. It stops at `max_ticks` or when every
hostile is gone.

The clock is a deterministic simulation clock, sim time equals tick count times
the tick interval, not wall-clock. Fusion coasting and time-to-impact depend on
the time delta, so a wall-clock base would make outcomes vary with CPU load and
break reproducibility.

Each tick, in order:

1. Advance the attacker. `HostileSwarm.advance(dt)` flies the red force one step
   with evasion and reactions.
2. Collect detections. `source.sample_once()` returns one tick of detections.
   `DegradationModel.apply` then drops a jam fraction and blanks blackout windows
   to model a contested, comms-denied picture.
3. Audit detections. When auditing is on, `record_detections` persists every raw
   contact before fusion.
4. Fuse. `TrackManager.update(detections, t)` folds detections into stable tracks.
5. Classify. `_classify_hostiles` marks every confirmed track HOSTILE. In this
   wargame every real airframe is an attacker, so a confirmed track is hostile by
   construction.
6. Assess threats. `threat.assess(tracks, site, t)` clusters tracks into swarms,
   infers swarm intent for situational awareness, scores each hostile track into a
   `Threat`, and ranks them.
7. Allocate and resolve. `_engage` runs the `LayeredAllocator` against ready
   defenders, commits capacity and reload, then resolves outcomes against the cost
   ledger, gated by lethal radius and target resistance.
8. Audit the tick. `record_tick` writes the engagement entities, threat and track
   rows, link rows, and one decision row per engagement with full lineage.
9. Queue events. Engagement events with lineage are staged for the OVERWATCH
   events DB and flushed asynchronously by the run loop.
10. Cool down. Reload timers tick down and finished defenders return to READY.
11. Build a `Frame`. Metrics are recomputed and a renderable frame is yielded.

## 4. Module map

| Module | Responsibility |
|---|---|
| `sensors/base.py` | `SensorSource` abstract contract, the one-engine seam |
| `sensors/sim_source.py` | Phenomenological multi-sensor simulator source |
| `sensors/real_source.py` | Deployable source: JSONL replay and live telemetry |
| `fusion/track_manager.py` | Multi-sensor fusion: cluster, gate, associate, confirm, merge, expire |
| `fusion/kalman.py` | Per-track constant-velocity Kalman filter and gating math |
| `threat/clustering.py` | Single-link spatial clustering of hostile tracks into swarms |
| `threat/intent.py` | Geometric and kinematic swarm intent inference |
| `threat/scoring.py` | Per-threat scoring and priority ranking |
| `threat/classifier.py` | `assess()` entry point: per-track threats with swarm SA |
| `defense/allocator.py` | `LayeredAllocator` and `CostLedger`, area then point assignment |
| `attacker/hostile_swarm.py` | Evasive, reactive, hardened, jam-resistant red force |
| `wargame/runner.py` | The tick loop, honest kill resolution, frame emission |
| `wargame/world.py` | Live world model assembled from a scenario |
| `wargame/scenario.py` | Scenario presets, effector wiring from calibration |
| `wargame/degradation.py` | Jam fraction and blackout windows on detections |
| `wargame/calibration.py` | Data-driven effector loader with provenance bands |
| `wargame/sweep.py` | CER sensitivity sweep with p10/p90 bands and crossover |
| `wargame/audit.py` | Entity-relational audit store, lineage, `reconstruct_chain` |
| `ontology_bridge.py` | Maps BULWARK objects onto OVERWATCH wire protocol |
| `config/effectors.yaml` | Calibrated effector data, every value an estimate with a band |

### Fusion detail

`TrackManager` runs predict, then associate, then correct, spawn, merge, and
expire. Detections from overlapping sensors are first single-link clustered and
fused into one measurement per object, so one drone yields one track, not one per
sensor. A single sensor reports each object once per tick, so two detections from
the same sensor never share a cluster. Association is global nearest-neighbor with
a chi-square gate (0.99 quantile, three degrees of freedom) blended with a
velocity-mismatch term to hold identity through a crossing. Confirmation is N-of-M
(default 3 of the last 5 ticks), so steady targets confirm and one-off clutter
never does. A missed track coasts on predicted state with growing covariance and
expires after a coast timeout. Merge fires only on true duplicates: tight
co-location within about two measurement sigmas held across several consecutive
ticks, so a real second drone in formation is never merged away.

### Threat detail

Each hostile track becomes one `Threat`, so an effector can target an individual
airframe and the detection-to-engagement lineage stays intact. Swarms and their
intent (saturation, waves, decoy, probe) are still inferred and stamped onto each
member threat as context. Score is a 0..1 blend of time-to-impact urgency, closing
speed, and value at risk. Threats are ranked most urgent first.

### Allocator detail

`LayeredAllocator` runs two phases. Phase one assigns cheap area effectors
(effect radius greater than zero, such as HPM and EW) to the densest clusters of
unengaged threats. This is a greedy max-coverage approximation that maximizes
drones neutralized per reusable shot, the layer that wins on cost. Phase two
assigns point effectors to the highest-value surviving threats with a Bertsekas
auction that solves the assignment near optimally. Cost discipline gates the
expensive kinetic layer: an interceptor fires only against an imminent leaker, and
never against a known decoy or a low-confidence track, so it does not blow the CER
on a cheap, fake, or uncertain target.

## 5. The cost-exchange model

The headline metric is the CER, defender dollars spent per attacker dollar of
airframe destroyed, computed in `_compute_metrics`. The numerator is
`ledger.defender_spent`, the sum of committed engagement costs. The denominator is
`attacker_dollars_destroyed`, the summed real airframe cost of killed drones, not
an endangered-asset figure. The ratio is `None` until something is destroyed.

The CER is emergent. The allocator decides the kill mix. Cheap area shots from HPM
and EW kill the cheap mass for almost nothing. Expensive kinetic interceptors fire
only when nothing cheaper can kill the target. The ratio is whatever that mix
produces against the raid, not a constant set anywhere in the code.

### The honest kill model

The allocator produces PENDING engagements. The runner resolves them and owns the
physical truth, in `_resolve_engagements` and `_apply_effect`. Two gates make the
kill honest:

- Radius gate. For each targeted threat the runner finds the nearest live drone
  within the effector lethal radius. Area effectors use their effect radius. A
  point effector uses a fixed 80 meter lethal radius. A shot at empty airspace
  kills nothing, so a miss lets the drone leak.
- Resistance. Kill probability is the effector base probability scaled by how the
  target resists that effector kind (`_resistance`). A jam-resistant drone defeats
  EW entirely (scale 0.0). A hardened drone largely shrugs off HPM (scale 0.15).
  Kinetic effectors work regardless (scale 1.0). This forces a hardened,
  jam-resistant raid onto the expensive kinetic layer, which is where the attacker
  tries to win the cost war.

The raid economy matches this. In `hostile_swarm.py` a plain drone costs the base
500 dollars and a decoy costs 50, but a jam-resistant or hardened airframe costs
6000 to 14000 dollars because it carries shielding, autonomy, or fiber-optic
control. So the cheap mass dies cheaply to area effects, and the few drones that
force a kinetic interceptor are themselves expensive, keeping the trade
defensible. The cost war is not a free win or a free loss.

### Calibration and sensitivity sweep

Effector performance is data driven. `config/effectors.yaml` holds every
effector field as a value plus a provenance source (vendor_spec, bench_test,
range_trial, physics_model, estimate) and a plausible min/max band.
`calibration.py` loads it into typed profiles, and scenario builders construct
effectors from calibration instead of from literals, so measured kill curves can
replace the current estimates with no code change.

`sweep.py` runs the wargame across a grid of effector parameters and seeds and
reports the CER as a distribution: mean with p10 and p90 bands, plus a crossover
report naming where mean CER reaches 1.0 and defense stops winning on cost. The
sweep varies effectors across their calibrated bands, not across arbitrary
numbers. This answers the honest question of how robust the CER is, instead of
shipping one optimistic figure.

Note: every value in `effectors.yaml` is currently an estimate. See section 9.

## 6. Data and lineage

`wargame/audit.py` is the explainable-and-replayable layer. It is a real
entity-relational ontology in SQLite, not one flat table. `detections`, `tracks`,
`threats`, and `engagements` are entity tables joined by declared FOREIGN KEY
relationships, with `track_detections` and `engagement_threats` as link tables.
Surrogate integer primary keys carry the natural keys that repeat across ticks.
Foreign keys are enforced on every connection. A `schema_meta` table records the
schema version so a reader can branch on it.

`reconstruct_chain(db_path, engagement_id)` traverses the foreign keys to answer
why one drone was engaged. It walks engagement to engagement_threats to threats to
tracks to track_detections to detections, returning the outcome, the primary
threat scored fields, every targeted threat with its kill flag, and every
contributing raw detection grouped by sensor. The full detection set survives even
though the in-memory `source_detection_ids` list is capped at 64, because
`link_tracks` appends the current ids each tick and the union across ticks
reconstructs the whole chain.

Run provenance: a `runs` table and a `run_id` on every decision row scope the
decision log per run, so several runs share one store without their after-action
logs colliding. The FK lineage uses globally unique surrogate row ids, so chain
reconstruction is collision-safe across runs on its own.

OVERWATCH event bridge: `ontology_bridge.py` maps BULWARK objects onto the
existing OVERWATCH wire protocol so engagements surface on the same HUD, roster,
and event log. `engagement_to_event` builds an event carrying the score, swarm id,
intent, and the contributing detection ids. The runner stages these each tick and
`emit_engagement_event` writes them to the live events DB asynchronously, so a
surfaced engagement is fully explainable on the HUD activity feed.

## 7. Performance

All optimizations are behavior-preserving. Each was committed to produce the same
matches and kills as the implementation it replaced, only faster.

- Spatial-hash pre-filter. Fusion clustering, gating, dedup, and merge all use a
  uniform spatial grid (`_SpatialGrid`) so each query scans the 27 neighbor cells,
  not every other contact. This keeps association near-linear at swarm scale
  instead of the full track-by-detection product.
- Per-track inverse covariance. The innovation covariance for gating depends only
  on the track and the nominal sensor sigma, not on the candidate measurement.
  `kalman.gate_inverse` computes it once per track per tick. This turns a per-pair
  linear solve into one inverse per track plus a cheap matrix product per
  candidate.
- Batched gate scoring. Candidate pairs are scored with vectorized numpy einsum
  over all pairs at once, removing per-pair Python and numpy-call overhead.
  Survivors are emitted in the original scan order so the greedy resolver breaks
  ties identically.
- Spatial-hash swarm clustering. Threat clustering uses union-find over a
  spatial-hash grid, yielding the identical clusters as the dense all-pairs scan
  in near-linear time.
- Reused auction rows. Every slot of one defender shares the same benefit row, so
  the auction matrix computes one row per unique defender and reuses the reference
  across its capacity copies.

Measured per-tick numbers from the development runs: fusion is about 88 ms at
1000 simultaneous contacts, with corresponding speedups to the full loop and the
test suite. I could not re-run benchmarks in this environment, so these figures
are taken from the change history that introduced the optimizations and should be
re-measured on the target host.

## 8. Verification

The suite is about 294 passing tests. It covers Kalman behavior, fusion track
accuracy and identity stability under noise and dropout, clutter rejection,
formation non-merge, crossing identity, threat scoring and intent, allocator
quality, the honest kill model end to end, audit lineage and chain
reconstruction, calibration loading, the sweep, and the sensor sources.

The suite splits fast from slow. `pytest.ini` defines a `slow` marker for the
full end-to-end scenario wargames. `pytest -m "not slow"` runs the fast unit and
component loop. `pytest` runs everything, the gate used in CI.

Runs are deterministic. The runner uses a sim clock, the swarm and sensors draw
from an injected seeded RNG, and the attacker evasion and reactions run on an
internal sim clock, so a fixed seed reproduces every run.

I could not execute the suite in this environment, so the 294 figure is the
reported count from the change history, not a count I verified by running pytest.

## 9. Honest limitations

- Effector kill curves are calibrated estimates, not measured data. Every value
  in `config/effectors.yaml` is marked an estimate drawn from open vendor
  literature and physics. The provenance and band machinery exists so real
  bench_test and range_trial data can replace them, but that data is not here yet.
  Any absolute CER claim inherits this uncertainty. The sweep exists to bound it.
- Pure-Python single node. The engine is single-process Python with numpy for the
  filter math. There is a real per-tick latency ceiling at swarm scale. The
  optimizations push it back but do not remove it. Hard real-time guarantees at
  the high end would need a faster runtime or distribution.
- Ontology versioning. The audit store scopes the decision log by `run_id` and
  uses globally unique surrogate keys for FK lineage. It does not version
  individual entities across runs beyond that run scoping. There is no per-entity
  history or temporal diff across runs.
- No live hardware. `RealSensorSource` proves the deployable seam through JSONL
  replay and a telemetry adapter. No physical radar, RF, or effector is wired in.
  The deployable path is demonstrated, not flown.

## 10. What was tried and rejected

Predictive hardness-aware allocation was implemented and then reverted. The idea
was to let the allocator predict target resistance and pre-route hardened or
jam-resistant drones to the kinetic layer instead of wasting a soft-kill shot
first. Measured against the wargame it was counterproductive, so it was reverted
while the unrelated cleanups from the same change were kept (commits "Cleanups and
predictive hardness-aware allocation" then "Revert predictive hardness-aware
allocation; keep the cleanups"). The shipped allocator stays resistance-blind and
lets the runner's honest kill resolution surface the cost of a wasted soft-kill
shot, which the cost discipline already accounts for. The lesson: measure the
change against the metric before keeping it.

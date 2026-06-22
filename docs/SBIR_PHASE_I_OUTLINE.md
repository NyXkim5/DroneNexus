# SBIR Phase I Proposal Outline
# Counter-UAS Software-Defined Command Layer (OVERWATCH)

---

## 1. Cover Page

| Field | Value |
|---|---|
| **Proposal Title** | OVERWATCH: Software-Defined Counter-UAS Command and Control Layer |
| **Topic Number** | [Insert DoD SBIR Topic Number, e.g., AF###-###] |
| **Topic Title** | Counter-UAS Software-Defined Command Layer |
| **Proposing Firm** | Archv LLC |
| **Principal Investigator** | Joonhyuk (Jay) Kim |
| **PI Email** | [Insert PI Email] |
| **PI Phone** | [Insert PI Phone] |
| **DUNS / UEI** | [Insert UEI] |
| **CAGE Code** | [Insert CAGE Code] |
| **Award Amount Requested** | $150,000 |
| **Period of Performance** | 6 months |
| **Place of Performance** | [Insert City, State] |

---

## 2. Technical Abstract (200 words max)

**Problem.** Current Counter-UAS (C-UAS) systems are hardware-locked, vendor-siloed, and expensive. A single kinetic engagement can cost $100K-$400K against a $500 commercial drone. Sensor data from RF receivers, radar, EO/IR, and acoustic detectors cannot be fused across vendor boundaries. Operators must manage multiple proprietary consoles with no unified threat picture.

**Innovation.** OVERWATCH is a software-defined C2 layer that fuses heterogeneous sensor inputs and commands heterogeneous effectors through a single decision engine. The system ingests ASTM F3411 Remote ID, DJI DroneID (OcuSync), visual detection via YOLOv11x, and radar tracks through a common SensorSource interface. A threat-intent classifier performs geometric and kinematic analysis to distinguish saturation attacks, decoy swarms, probes, and wave patterns. A greedy allocation engine assigns effectors to threats under range, capacity, and cost constraints. The full kill chain (detect, track, classify, authorize, engage) executes in under 2 seconds.

**Phase I Approach.** Validate OVERWATCH against live RF sensors and physical DJI Mini targets. Demonstrate full kill chain on edge hardware (NVIDIA Jetson Orin). Measure detection rates, false alarm rates, and engagement timing.

**Impact.** Reduce C-UAS cost per engagement by 10x through software-optimized allocation and decoy discrimination. Enable any-sensor, any-effector interoperability for DoD and allied forces.

---

## 3. Identification and Significance of the Problem

### 3.1 The C-UAS Cost Inversion

Small commercial UAS (sUAS) cost $300-$2,000. Current kinetic defeat systems (missiles, directed energy) cost $30K-$400K per engagement. At scale, the attacker wins economically. A 20-drone swarm costing $10K total can exhaust $2M+ in defensive interceptors. This cost inversion is the central problem.

### 3.2 Vendor Lock-In and Data Silos

Deployed C-UAS systems (LIDS, NINJA, CORIAN) each use proprietary sensor formats, proprietary displays, and proprietary command interfaces. When multiple systems protect the same site, there is no unified operating picture. Operators switch between consoles. Threat tracks are duplicated or missed at handoff boundaries.

### 3.3 Swarm Threat Escalation

Adversaries are moving from single-UAS incursions to coordinated swarms. DoD threat assessments identify saturation attacks (many drones, tight formation, simultaneous arrival) and decoy-laced swarms (expendable drones masking high-value strike assets) as near-term threats. Existing C-UAS systems have no software layer to classify swarm intent or optimize allocation across a mixed threat set.

### 3.4 Why Software, Not Hardware

The hardware layer (sensors, effectors) is maturing. What is missing is the software connective tissue: a sensor-agnostic fusion engine, a threat classifier that understands swarm geometry, and an allocator that minimizes cost while maximizing probability of kill. This is a software problem.

---

## 4. Phase I Technical Objectives

1. **OBJ-1:** Demonstrate live RF ingestion of ASTM F3411 ODID and DJI DroneID frames from an AntSDR E200 receiver with position decode accuracy within 10m CEP.
2. **OBJ-2:** Validate YOLOv11x visual drone detection at 300m+ range against a DJI Mini 3 target with Pd > 0.85 and Pfa < 0.01.
3. **OBJ-3:** Execute a full detect-track-classify-authorize-engage kill chain in under 3 seconds from first detection to engagement command.
4. **OBJ-4:** Achieve RF+visual sensor fusion correlation rate above 90% for co-located targets.
5. **OBJ-5:** Deploy the complete system on a single NVIDIA Jetson Orin Nano at 10 Hz update rate.

---

## 5. Phase I Work Plan

### Task 1: RF Sensor Integration (Months 1-2)

**Objective.** Integrate AntSDR E200 RF receiver with OVERWATCH via the existing AntSDRSensorSource TCP adapter. Validate live decoding of ASTM F3411 Remote ID and DJI OcuSync DroneID binary frames.

**Technical Approach.**
- Connect AntSDR E200 to OVERWATCH via TCP on port 41030.
- Decode DJI DroneID binary frames (minimum 227-byte payload) using the dji_decoder module. Extract UAS position, operator position, home position, serial number, and flight path.
- Decode ASTM F3411 ODID packed messages (25-byte message units) using the odid_decoder module. Parse BasicID, Location, System, SelfID, and OperatorID message types.
- Validate position accuracy against GPS ground truth from the target drone's onboard telemetry.
- Measure detection range, latency from transmission to decoded Detection object, and decode success rate.

**Deliverables.** RF sensor integration test report. Detection range and accuracy measurements. Updated AntSDRSensorSource with any field-discovered protocol fixes.

**Go/No-Go Criteria.** Decode success rate > 95% within line-of-sight range. Position accuracy < 10m CEP.

### Task 2: Visual Detection Validation (Months 2-3)

**Objective.** Validate the YOLOv11x drone detector node against physical DJI Mini 3 targets in an outdoor environment at operationally relevant ranges.

**Technical Approach.**
- Deploy the detector_node (drone_perception package) on Jetson Orin with TensorRT optimization.
- Conduct outdoor flight tests at 100m, 200m, 300m, and 500m ranges in varying lighting conditions (dawn, midday, dusk).
- Record detection probability (Pd), false alarm rate (Pfa), classification accuracy (drone vs. bird vs. clutter), and inference latency.
- Fine-tune the model on collected field data if Pd at 300m falls below 0.85.

**Deliverables.** Visual detection performance report with Pd/Pfa curves by range and lighting condition. Optimized TensorRT model weights for Jetson Orin.

**Go/No-Go Criteria.** Pd > 0.85 at 300m. Pfa < 0.01. Inference latency < 50ms per frame on Jetson Orin.

### Task 3: Full Kill Chain Demonstration (Months 3-4)

**Objective.** Demonstrate the complete OVERWATCH kill chain (detect, track, classify, authorize, engage) against a live DJI Mini 3 target drone using combined RF and visual sensors.

**Technical Approach.**
- Fuse RF detections (AntSDR) and visual detections (YOLOv11x) into unified tracks using the swarm coordinator's track management pipeline.
- Run threat intent classification using the geometric/kinematic classifier (intent.py) on live tracks. Validate correct classification of single-drone probe behavior.
- Execute the greedy allocator (allocator.py) to assign a simulated effector to the classified threat under range and cost constraints.
- Measure end-to-end latency from first sensor detection to engagement command output.
- Publish engagement status via Cursor on Target (CoT) XML to a TAK server for operator situational awareness.

**Deliverables.** Kill chain timing report. Recorded engagement sequence with CoT event log. Video of live demonstration.

**Go/No-Go Criteria.** End-to-end kill chain < 3 seconds. Correct threat classification. CoT events received by TAK client.

### Task 4: Sensor Fusion Benchmarking (Months 4-5)

**Objective.** Quantify sensor fusion performance by measuring RF+visual track correlation rate, duplicate track rate, and false positive rate across multiple test flights.

**Technical Approach.**
- Fly 20+ test sorties with DJI Mini 3 at varying ranges, altitudes, and approach vectors.
- Record all raw sensor detections and fused tracks.
- Compute correlation rate: percentage of RF detections that fuse with a visual detection within 5 seconds and 50m spatial gate.
- Compute false positive rate: fused tracks with no corresponding ground-truth target.
- Compare single-sensor (RF only, visual only) performance against fused performance.

**Deliverables.** Sensor fusion performance report. Statistical analysis with confidence intervals. Raw data archive.

**Go/No-Go Criteria.** Correlation rate > 90%. False positive rate < 5%.

### Task 5: Edge Deployment and Prototype Delivery (Months 5-6)

**Objective.** Package the complete OVERWATCH system for edge deployment on NVIDIA Jetson Orin Nano. Deliver a field-ready prototype.

**Technical Approach.**
- Containerize all OVERWATCH services (FastAPI backend, sensor adapters, detector node, coordinator, allocator) in Docker images optimized for ARM64/Jetson.
- Validate 10 Hz coordinator tick rate on Jetson Orin under simultaneous RF and visual sensor load.
- Integrate the BULWARK HUD (bulwark.html) for local operator display with track confidence rings, defender status panel, and engagement feed.
- Conduct a 4-hour continuous run to validate stability, memory usage, and thermal performance.
- Write deployment guide and operator manual.

**Deliverables.** Jetson Orin edge deployment package (Docker images + configuration). Stability test report. Operator manual. Source code with 1500+ automated tests.

**Go/No-Go Criteria.** 10 Hz update rate sustained for 4 hours. No memory leaks. System recoverable from sensor dropout within 5 seconds.

---

## 6. Related Work

### 6.1 Existing C-UAS Systems

| System | Vendor | Limitation |
|---|---|---|
| LIDS | SRC Inc. | Radar-only, no RF ID decode, proprietary C2 |
| NINJA | SRC Inc. | Kinetic only, no sensor fusion layer |
| CORIAN | Northrop Grumman | Fixed-site, high cost, closed architecture |
| DroneShield | DroneShield Ltd. | RF detection only, no effector integration |
| Dedrone | Dedrone GmbH | Detection and alerting, no autonomous engagement |

### 6.2 Open Standards Leveraged

- **ASTM F3411** (Remote ID): OVERWATCH decodes all five ODID message types natively.
- **Cursor on Target (CoT)**: Bidirectional TAK integration for interoperability with existing DoD C2 systems.
- **MAVLink / MSP**: Effector command protocols for drone-based and ground-based countermeasures.

### 6.3 OVERWATCH Differentiators

- Sensor-agnostic: pluggable SensorSource interface accepts any sensor type.
- Swarm-aware: geometric intent classifier distinguishes saturation, decoy, wave, and probe tactics.
- Cost-optimized: allocator includes 8x overspend gate to prevent wasting kinetic interceptors on decoys.
- AI-native: Gymnasium RL environment for adversarial red-team training against the defense.

---

## 7. Phase I Deliverables

| # | Deliverable | Format |
|---|---|---|
| D1 | Working prototype on Jetson Orin edge hardware | Docker images + deployment scripts |
| D2 | Field test report: detection rates, false alarm rates, kill chain timing | PDF report |
| D3 | Sensor fusion performance analysis (RF+visual correlation, false positive rate) | PDF report with raw data |
| D4 | Software source code with 1500+ automated tests | Git repository |
| D5 | Architecture document for Phase II scaling | PDF document |
| D6 | Operator manual for edge deployment | PDF document |

---

## 8. Key Personnel

### 8.1 Principal Investigator

**Joonhyuk (Jay) Kim** -- CEO, Archv LLC

[Placeholder for PI bio. Include:
- Relevant technical background (AI/ML, defense, systems engineering)
- Prior government contract experience if any
- Education
- Publications or patents if applicable
- Percentage of time dedicated to this effort: 50% (0.5 FTE)]

### 8.2 Additional Team Members

**[Name]** -- [Title/Role]

[Placeholder for contractor or team member bio. Include:
- Relevant C-UAS, RF engineering, or computer vision experience
- Role on this effort
- Percentage of time dedicated]

### 8.3 Consultants / Advisors

[Placeholder for subject matter experts if applicable. Note: consultant costs must be included in "Other" budget category.]

---

## 9. Facilities and Equipment

### 9.1 Development Environment

| Item | Specification | Purpose |
|---|---|---|
| Apple M1 Max MacBook Pro | 64GB RAM, 10-core GPU | Primary development workstation |
| GPU Workstation | [Specify GPU, e.g., RTX 4090] | Model training and inference benchmarking |
| Cloud Compute | [AWS/Azure GPU instances] | Extended RL training runs |

### 9.2 Test Equipment

| Item | Specification | Purpose |
|---|---|---|
| AntSDR E200 | SDR receiver, 70 MHz - 6 GHz | RF detection of DJI DroneID and ODID signals |
| DJI Mini 3 | 249g sUAS target drone | Live target for detection and tracking tests |
| NVIDIA Jetson Orin Nano | 8GB, 40 TOPS | Edge deployment target platform |
| Directional Antennas | [Specify band/gain] | Extended RF detection range |
| USB GPS Receiver | u-blox based | Ground truth positioning |

### 9.3 Test Range

[Placeholder: Identify an FAA Part 107 compliant outdoor test location. Options include:
- Private property with appropriate airspace clearance
- FAA-approved UAS test range
- DoD installation with CUAS test authority (if available through program sponsor)]

### 9.4 Software Stack

| Component | Technology | License |
|---|---|---|
| Backend framework | Python 3.11, FastAPI, asyncio | MIT / BSD |
| Object detection | YOLOv11x, PyTorch, TensorRT | AGPL-3.0 (YOLOv11), BSD (PyTorch) |
| RL training | Gymnasium, Stable-Baselines3 | MIT |
| Visualization | CesiumJS, HTML/CSS/JS | Apache 2.0 |
| Containerization | Docker, Docker Compose | Apache 2.0 |
| CoT/TAK integration | Custom XML formatter, TCP/UDP | N/A (custom) |

---

## 10. Cost Breakdown ($150,000)

| Category | Amount | Justification |
|---|---|---|
| **Personnel** | $100,000 | PI at 0.5 FTE for 6 months ($60K). One software/ML contractor at 0.3 FTE for 6 months ($40K). |
| **Equipment** | $15,000 | AntSDR E200 + antennas ($3K). DJI Mini 3 + spare batteries ($1.5K). NVIDIA Jetson Orin Nano Developer Kit ($500). Accessories, cables, mounts ($1K). Additional sensors/spares ($9K). |
| **Travel** | $10,000 | Test range visits (4 trips, $1.5K each = $6K). Customer/sponsor meetings (2 trips, $2K each = $4K). |
| **Other Direct Costs** | $15,000 | Cloud GPU compute for RL training ($5K). Software licenses ($2K). Liability insurance for UAS operations ($3K). Test range fees ($3K). Report production ($2K). |
| **Overhead** | $10,000 | General and administrative (office, communications, accounting). |
| **Total** | **$150,000** | |

---

## 11. Phase II Plan

### 11.1 Phase II Objectives

If Phase I validates the core OVERWATCH architecture against live targets with a single RF+visual sensor pair, Phase II will scale the system across four dimensions:

1. **Multi-sensor fusion.** Integrate radar (COTS pulse-Doppler), acoustic arrays, and additional RF bands beyond 2.4/5.8 GHz. Target: 4+ heterogeneous sensor types fused in real time.
2. **Multi-site federation.** Connect multiple OVERWATCH nodes across geographically distributed sites with a federated track database and coordinated engagement deconfliction.
3. **Hardened deployment.** Achieve CMMC Level 2 certification. Harden the edge deployment for ATEC environmental qualification (temperature, vibration, EMI).
4. **Pilot deployment.** Deploy OVERWATCH at a DoD installation with operational C-UAS authority for a 90-day pilot evaluation.

### 11.2 Phase II Scope

| Task | Timeline | Description |
|---|---|---|
| Radar + acoustic integration | Months 1-4 | Add SensorSource adapters for COTS radar and acoustic sensors. Validate fusion with RF+visual. |
| RL adversary hardening | Months 2-6 | Train RL adversary across all six benchmark scenarios (skirmish through saturation). Use curriculum learning to discover allocator weaknesses. |
| Multi-site federation | Months 4-8 | Design and implement federated track exchange protocol. Test with 3 simulated sites. |
| CMMC Level 2 | Months 6-12 | Implement access controls, audit logging, encryption at rest (AES-256), TLS 1.2+ in transit. Conduct gap assessment and remediation. |
| Pilot deployment | Months 9-18 | Deploy at DoD partner site. Conduct 90-day operational evaluation. Collect performance data. |

### 11.3 Phase II Budget Target

$750,000 over 18 months.

---

## 12. Commercialization Plan

### 12.1 Target Markets

| Market | Size Estimate | Entry Strategy |
|---|---|---|
| **DoD installations** | 400+ bases worldwide | SBIR Phase II/III transition. Direct engagement with AFRL, PEO-MS, JCO. |
| **Critical infrastructure** | Airports, power plants, refineries, stadiums | Commercial license through direct sales and integrator partnerships. |
| **Allied nations** | NATO + Five Eyes | FMS/DCS channels. ITAR compliance for export-controlled components. |
| **Federal law enforcement** | DHS, CBP, Secret Service | Existing DHS SBIR pathways. SAFETY Act designation pursuit. |

### 12.2 Business Model

- **Annual software license.** $50,000 - $200,000 per site depending on sensor count and threat volume.
- **Integration services.** $25,000 - $75,000 per deployment for sensor integration, calibration, and operator training.
- **Support and updates.** 20% of license fee annually for software updates, threat library updates, and technical support.
- **Edge hardware bundle (optional).** Jetson Orin kit with pre-loaded OVERWATCH image. $5,000 - $15,000 per unit.

### 12.3 Competitive Advantages

| Advantage | Detail |
|---|---|
| **10x lower cost per engagement** | Software-optimized allocation avoids wasting high-cost effectors on decoys. Overspend gate prevents cost ratio inversion. |
| **Open architecture** | Pluggable SensorSource and effector interfaces. No vendor lock-in. Customers retain sensor choice. |
| **AI-native** | Swarm intent classifier, RL-trained adversary for continuous red-teaming, Gymnasium environment for customer-specific threat modeling. |
| **TAK interoperable** | Bidirectional CoT integration. Drops into existing DoD C2 ecosystem without replacing operator tools. |
| **Edge-deployable** | Runs on a $500 Jetson Orin. No cloud dependency. Operates in denied/degraded/disconnected environments. |
| **Proven at scale** | Wargame engine validated across 6 scenarios up to 1,000 simultaneous threats with 83% intercept rate at 0.88 cost exchange ratio. |

### 12.4 Intellectual Property

OVERWATCH software is proprietary to Archv LLC. The firm retains all IP rights per standard SBIR data rights provisions (DFARS 252.227-7018). Open-source components (PyTorch, Gymnasium, FastAPI) are used under permissive licenses (MIT, BSD, Apache 2.0) that permit commercial redistribution.

### 12.5 Path to Revenue

| Milestone | Timeline | Revenue |
|---|---|---|
| Phase I complete | Month 6 | $150K (SBIR award) |
| Phase II award | Month 8 | $750K (SBIR award) |
| First commercial pilot | Month 12 | $75K (integration + license) |
| Phase III / production contract | Month 18-24 | $500K+ (DoD procurement) |
| 5 commercial sites | Month 24-36 | $500K ARR |

---

## 13. Prior, Current, and Pending Support

[Disclose all current and pending federal awards. If none, state: "Archv LLC has no current or pending federal awards that overlap with this proposal."]

---

## 14. References

[Placeholder: Include relevant references such as:]

1. ASTM F3411-22a, Standard Specification for Remote ID and Tracking.
2. DoD Counter-Small Unmanned Aircraft Systems Strategy, January 2021.
3. Joint Counter-UAS Office (JCO) Approved Systems List.
4. MIL-STD-6016 (Link 16) and Cursor on Target specification for interoperability context.

---

## Appendix A: Current Software Maturity

OVERWATCH exists today as a functional software system with the following verified metrics:

| Metric | Value |
|---|---|
| Automated test count | 1,066 (988 fast + 78 slow/integration) |
| Sensor adapters implemented | 4 (AntSDR TCP, UDP RID, ODID decoder, DJI decoder) |
| Wargame scenarios benchmarked | 6 (skirmish through 1,000-threat saturation) |
| Intercept rate (500-threat contested) | 83.4% mean |
| Cost exchange ratio (500-threat contested) | 0.82 mean |
| Zero-leaker scenarios (skirmish, probe) | 0 leakers across all runs |
| Kill chain components | Detect, track, classify (5 intent types), allocate, engage, assess (BDA) |
| CoT/TAK integration | Bidirectional (publish + receive), 6 CoT event types |
| RL adversary environment | Gymnasium-compatible, 6-action discrete space, PPO/DQN trained |
| Edge target | NVIDIA Jetson Orin Nano (40 TOPS) |

This is not a paper design. Phase I funds will validate the existing software against physical hardware and live targets, not build the software from scratch.

---

## Appendix B: Proposal Compliance Checklist

- [ ] Technical abstract within 200-word limit
- [ ] Phase I work plan includes specific tasks, milestones, and go/no-go criteria
- [ ] Cost breakdown totals to requested amount
- [ ] Key personnel identified with qualifications
- [ ] Facilities and equipment described
- [ ] Phase II plan included
- [ ] Commercialization plan included
- [ ] Prior/current/pending support disclosed
- [ ] Data rights assertions included (DFARS 252.227-7018)
- [ ] Company certifications current (SAM.gov, SBA)
- [ ] Page count within solicitation limits (typically 25 pages for Phase I)
- [ ] Submitted through DSIP (Defense SBIR/STTR Innovation Portal)

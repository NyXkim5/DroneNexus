# OVERWATCH -- Counter-Swarm Defense Engine

**Software-defined command and control for counter-drone operations.**

---

## The Problem

The counter-UAS market is valued at $6.6B today and projected to reach $20B by 2030. Current solutions fall into two extremes:

- **Detection-only platforms** (Dedrone, others) that alert operators but take no action.
- **Full-stack hardware systems** (Anduril, others) that cost $10M+ per site and lock customers into proprietary ecosystems.

No affordable, sensor-agnostic, effector-agnostic software command layer exists. Defenders are forced to choose between awareness without action or action at prohibitive cost.

## The Solution

OVERWATCH is the software brain for counter-drone defense. It fuses ANY sensor, commands ANY effector, and drives the cost-exchange ratio below 1.0 so that defense always wins on economics.

OVERWATCH sits between your existing sensors and effectors. It ingests tracks from any source, classifies threats autonomously, allocates the cheapest effective response, and logs every decision for legal review.

## Key Capabilities

- **Multi-sensor fusion** -- RF, visual, thermal, acoustic, and Remote ID (ASTM F3411, DJI DroneID)
- **94.9% mAP drone detection** -- YOLOv11x model trained on 75,000+ labeled images
- **Autonomous threat classification and prioritization** -- Intent analysis separates real threats from decoys and benign traffic
- **Layered effector allocation** -- Area-effect EW for swarms, point kinetic for high-value threats, with overspend protection
- **Rules of Engagement with full audit trail** -- Every engagement decision is logged with timestamp, operator ID, and tamper-proof hash chain
- **Real-time 3D visualization** -- CesiumJS-powered HUD with confidence rings, heatmaps, and swarm cluster overlays
- **TAK/CoT interoperability** -- Bidirectional Cursor-on-Target integration for joint operations
- **Edge deployable** -- Runs on NVIDIA Jetson Orin, standard Docker, or cloud infrastructure

## Performance Metrics

| Metric | Value |
|--------|-------|
| Automated tests | 1,523 |
| Cost exchange ratio | 0.54 (defense wins on cost) |
| Kill chain speed | < 5 seconds average |
| Simultaneous tracks | 1,000+ |
| Sensor-to-decision latency | Sub-second |

## Deployment Options

| Model | Description |
|-------|-------------|
| **Cloud SaaS** | Multi-tenant, zero hardware, API-driven |
| **On-premise** | Docker Compose on customer hardware |
| **Edge** | NVIDIA Jetson Orin for forward-deployed or mobile use |
| **Embedded** | Integrated into existing C2 systems via SDK |

## Architecture

```
Sensors (RF, Camera, Acoustic, RID)
        |
   [ OVERWATCH ]
   - Fusion Engine
   - Threat Classifier
   - Defense Allocator
   - ROE Engine
   - Audit Logger
        |
Effectors (EW, Kinetic, Net, Laser)
```

## Contact

For pricing, demos, or partnership inquiries:

- Email: contact@overwatch-platform.com
- Web: https://overwatch-platform.com

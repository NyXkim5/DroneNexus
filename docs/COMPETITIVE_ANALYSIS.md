# OVERWATCH Competitive Analysis

## Market Position

OVERWATCH occupies a unique position in the counter-UAS market: a pure software command layer that is sensor-agnostic, effector-agnostic, and priced for broad adoption. Competitors are either detection-only or vertically integrated hardware vendors.

## Feature Comparison

| Feature | OVERWATCH | Dedrone | DroneShield | Anduril | D-Fend |
|---------|-----------|---------|-------------|---------|--------|
| **Detection** | Multi-sensor fusion (RF, visual, thermal, acoustic, RID) | RF + Radar | RF + Radar + Camera | Full stack (proprietary sensors) | RF only |
| **Classification** | AI-driven (94.9% mAP, YOLOv11x) | Basic signature matching | Basic signature matching | AI-driven | Protocol analysis |
| **Effector Control** | Any effector (modular adapter layer) | None (detection only) | Basic jamming | Proprietary effectors only | RF takeover |
| **Cost** | ~$50K/yr software license | $200K--$1M per site | $500K--$2M per site | $10M+ per deployment | $1M--$5M per site |
| **Deployment** | Edge / Cloud / Hybrid | On-premise only | On-premise only | Custom integration | On-premise only |
| **Open Architecture** | Yes (plugin system, REST API, SDK) | No | No | Partial (Lattice SDK) | No |
| **TAK Integration** | Native bidirectional CoT | Plugin (limited) | No | Native (Lattice) | No |
| **Swarm Handling** | Autonomous layered response (1,000+ tracks) | Alert only | Limited | Yes | Limited |
| **Audit Trail** | Tamper-proof hash chain, full ROE logging | Basic logging | Basic logging | Yes | Basic logging |
| **Edge Compute** | Jetson Orin, Docker | No | No | Custom hardware | No |

## Competitive Advantages

### vs. Dedrone
Dedrone detects but does not act. OVERWATCH closes the loop from detection through effector engagement. Organizations using Dedrone for awareness can add OVERWATCH as the command layer without replacing existing sensors.

### vs. DroneShield
DroneShield bundles detection hardware with basic jamming. OVERWATCH provides superior AI classification and supports any effector type, not just RF jamming. OVERWATCH costs a fraction of a DroneShield deployment.

### vs. Anduril
Anduril delivers a complete system at $10M+ per site with proprietary hardware lock-in. OVERWATCH provides the same software intelligence at 1/200th the cost and integrates with hardware the customer already owns. For organizations that cannot justify Anduril's price point or prefer vendor independence, OVERWATCH is the alternative.

### vs. D-Fend
D-Fend specializes in RF protocol takeover for specific drone models. OVERWATCH is model-agnostic and supports multiple effector types beyond RF takeover. D-Fend's approach fails against drones with unknown protocols or autonomous flight modes.

## Total Cost of Ownership (3-Year)

| Solution | Year 1 | Year 2 | Year 3 | 3-Year Total |
|----------|--------|--------|--------|--------------|
| **OVERWATCH** | $50K | $50K | $50K | $150K |
| **Dedrone** | $500K | $200K | $200K | $900K |
| **DroneShield** | $1.5M | $300K | $300K | $2.1M |
| **D-Fend** | $2M | $500K | $500K | $3M |
| **Anduril** | $10M+ | $2M | $2M | $14M+ |

*Estimates based on publicly available pricing and industry reports. Actual costs vary by site configuration and scale.*

## Target Customers

- **Military installations** that need C-UAS but lack Anduril-level budgets
- **Critical infrastructure** (airports, power plants, stadiums) with existing sensor investments
- **Law enforcement and homeland security** agencies requiring portable, rapid-deploy capability
- **Allied nations** seeking non-ITAR, exportable C-UAS software
- **Defense integrators** building custom C-UAS solutions who need a proven software core

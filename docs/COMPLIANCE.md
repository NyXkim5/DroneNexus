# OVERWATCH Compliance Documentation

## 1. Software Bill of Materials (SBOM)

OVERWATCH supports automated SBOM generation in CycloneDX and SPDX formats.

**Generation methods:**

```bash
# CycloneDX via syft (container image)
syft overwatch:latest -o cyclonedx-json > sbom-cyclonedx.json

# CycloneDX via cdxgen (source)
cdxgen -t python -o sbom-cyclonedx.json

# SPDX via syft
syft overwatch:latest -o spdx-json > sbom-spdx.json
```

**Dependency sources scanned:**
- `requirements.txt` (Python backend)
- `package.json` (Electron HUD)
- `Dockerfile` (base image layers)
- ROS2 package manifests (`package.xml`)

SBOMs should be regenerated on every release and archived alongside the release artifact.

---

## 2. NIST SP 800-171 Control Mapping

OVERWATCH addresses the following NIST 800-171 control families. This is a partial mapping covering controls currently implemented. Controls not listed require further implementation or are not applicable.

### 3.1 Access Control

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.1.1 Limit system access to authorized users | Implemented | JWT authentication required for all API endpoints and WebSocket connections |
| 3.1.2 Limit system access to authorized functions | Implemented | Role-based access control (operator, viewer) enforced at API and UI layers |
| 3.1.5 Least privilege | Implemented | Viewer role cannot issue commands. Operator role cannot modify system config. |
| 3.1.7 Prevent non-privileged users from executing privileged functions | Implemented | ROE engagement commands restricted to operator role with JWT validation |

### 3.3 Audit and Accountability

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.3.1 Create and retain system audit logs | Implemented | All ROE decisions, engagement commands, and operator actions logged to SQLite event database |
| 3.3.2 Ensure actions can be traced to individual users | Implemented | Every audit record includes operator ID and timestamp |
| 3.3.3 Review and update audit events | Implemented | Tamper-proof hash chain on audit records. Each entry includes SHA-256 hash of previous entry. |
| 3.3.4 Alert on audit process failure | Partial | Hash chain integrity checked on read. Automated alerting planned for future release. |

### 3.4 Configuration Management

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.4.1 Establish and maintain baseline configurations | Implemented | Docker containers with pinned versions. Infrastructure as code via Docker Compose. |
| 3.4.2 Establish and enforce security configuration settings | Implemented | Environment variables via `.env` files. No hardcoded secrets. CI/CD enforces lint and test gates. |
| 3.4.5 Define and enforce access restrictions for change | Implemented | Branch protection on main. All changes require passing CI (1,523 automated tests). |

### 3.5 Identification and Authentication

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.5.1 Identify system users | Implemented | JWT tokens with user identity claims |
| 3.5.2 Authenticate users | Implemented | SHA-256 hashed passwords. JWT token issuance on successful authentication. |
| 3.5.3 Use multi-factor authentication | Planned | Not yet implemented. Roadmap item for FY2027. |

### 3.13 System and Communications Protection

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.13.1 Monitor and protect communications at system boundaries | Implemented | TLS 1.2+ enforced on all external connections. Nginx reverse proxy terminates TLS. |
| 3.13.8 Implement cryptographic mechanisms to prevent unauthorized disclosure | Implemented | AES-256 encryption at rest for sensitive configuration. TLS 1.2+ for all data in transit. |
| 3.13.11 Employ FIPS-validated cryptography | Partial | Standard library cryptography used. FIPS 140-2 validated module integration planned. |

### 3.14 System and Information Integrity

| Control | Status | Implementation |
|---------|--------|----------------|
| 3.14.1 Identify and correct system flaws in a timely manner | Implemented | 1,523 automated tests run on every commit via CI. Dependency scanning via SBOM tooling. |
| 3.14.2 Provide protection from malicious code | Implemented | Container isolation. No arbitrary code execution. Input validation on all API endpoints. |
| 3.14.6 Monitor the system | Implemented | Real-time WebSocket health metrics. Per-client rate limiting and bandwidth tracking. |

### Controls Not Yet Addressed

The following control families require additional work before claiming compliance:

- **3.2 Awareness and Training** -- Operator training program not yet formalized
- **3.6 Incident Response** -- IR plan drafted but not tested
- **3.7 Maintenance** -- Remote maintenance procedures not documented
- **3.8 Media Protection** -- Removable media policy not defined
- **3.9 Personnel Security** -- Not applicable to software (organizational control)
- **3.10 Physical Protection** -- Dependent on deployment environment (customer responsibility)
- **3.11 Risk Assessment** -- Formal risk assessment not yet conducted
- **3.12 Security Assessment** -- Third-party assessment not yet conducted

---

## 3. ITAR / Export Control Self-Assessment

### Classification: EAR99 (Not Export Controlled)

**Rationale:**

OVERWATCH is a software application that processes publicly available protocols and data formats. It does not contain or depend on export-controlled technology.

**Protocols processed:**
- ASTM F3411 Remote ID -- Published open standard
- DJI DroneID -- Publicly documented protocol
- Cursor-on-Target (CoT) -- Unclassified XML schema
- MAVLink -- Open-source communication protocol
- MSP (MultiWii Serial Protocol) -- Open-source protocol

**Algorithms used:**
- YOLOv11 -- Open-source object detection (Ultralytics, AGPL-3.0)
- PPO/DQN -- Standard reinforcement learning algorithms
- SHA-256 -- Standard cryptographic hash
- JWT -- Open standard (RFC 7519)

**What OVERWATCH does NOT contain:**
- No classified or controlled hardware interfaces
- No missile guidance or fire control algorithms subject to ITAR Category IV
- No signals intelligence (SIGINT) capabilities subject to USML Category XI
- No encryption algorithms beyond standard commercial implementations (EAR Category 5 Part 2 exemptions apply)

**Recommendation:** File a Commodity Jurisdiction (CJ) request with DDTC to obtain formal confirmation of EAR99 classification before international sales. Consult export control counsel for specific destination countries.

---

## 4. Security Contact

Report security vulnerabilities to:

- **Email:** security@overwatch-platform.com
- **Response SLA:** Acknowledgment within 48 hours. Triage within 5 business days.
- **PGP Key:** See `/Users/jay/DroneNexus/SECURITY.md` for PGP key and disclosure process.

---

## 5. Document Control

| Field | Value |
|-------|-------|
| Document Owner | Archv LLC |
| Last Updated | 2026-06-22 |
| Review Cycle | Quarterly |
| Classification | Company Confidential |

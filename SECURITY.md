# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x (current) | Yes |
| < 1.0 | No |

## Reporting a Vulnerability

If you discover a security vulnerability in OVERWATCH, please report it responsibly. Do not open a public GitHub issue.

### How to Report

1. Email **security@overwatch-platform.com** with a description of the vulnerability.
2. Include steps to reproduce, affected components, and potential impact.
3. If possible, encrypt your report using the PGP key below.

### What to Expect

- **Acknowledgment:** Within 48 hours of receipt.
- **Triage:** Within 5 business days, we will assess severity and assign a tracking ID.
- **Resolution:** Critical vulnerabilities targeted for patch within 14 days. High severity within 30 days.
- **Disclosure:** We will coordinate with you on public disclosure timing. We request a 90-day disclosure window.

### What Qualifies

- Authentication or authorization bypasses
- Remote code execution
- Data exposure (audit logs, configuration, operator credentials)
- Denial of service against the command and control pipeline
- Tampering with the audit hash chain
- WebSocket injection or privilege escalation

### What Does Not Qualify

- Vulnerabilities in dependencies without a demonstrated exploit path in OVERWATCH
- Issues requiring physical access to the deployment host
- Social engineering attacks
- Findings in third-party components not bundled with OVERWATCH (report these upstream)

## Security Architecture Summary

- All API endpoints require JWT authentication.
- Role-based access control separates operator and viewer permissions.
- Audit trail uses a tamper-proof SHA-256 hash chain.
- TLS 1.2+ required for all external connections.
- No secrets stored in code or version control.
- 1,523 automated tests run on every commit.

## PGP Key

```
[PLACEHOLDER -- Generate and insert production PGP public key before first release]

Key ID: 0x________________
Fingerprint: ____ ____ ____ ____ ____ ____ ____ ____ ____ ____
```

## Contact

- **Security reports:** security@overwatch-platform.com
- **General inquiries:** contact@overwatch-platform.com

# Security Policy

## Supported versions

Security fixes are applied to the newest published ShangBackground release and the current default branch.
Older releases may receive a fix only when the same patch can be backported safely.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability that could put users at risk.
Use GitHub's **Report a vulnerability** / private vulnerability reporting feature on this repository's Security page.
Include the affected platform and version, reproduction steps, expected impact, and any logs with personal paths removed.

The maintainers will acknowledge a complete report when it is reviewed, coordinate validation privately, and publish a security advisory when a fix is available. Do not include secrets, private files, or third-party personal data in a report.

## Scope

Reports about process termination, local IPC authorization, update/download integrity, release packaging, startup persistence, or command execution are in scope. Reports that require an already-compromised administrator/root account without crossing a new security boundary may be treated as hardening suggestions rather than vulnerabilities.

## Build and native runtime trust

Release builds must use the shared `build_tools/buildlib/` plan. Compatibility entry points must not introduce separate hidden arguments. Bundled MPV payloads are accepted only after platform, architecture and structure validation; network download is explicit and may be pinned with SHA-256. Build logs and vulnerability reports should redact personal media paths and local IPC identifiers.

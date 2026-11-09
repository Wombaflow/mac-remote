# Security Policy

## Supported versions

Only the latest release receives security fixes.

## Threat model

Mac Remote lets a device on your local network control your Mac's mouse and keyboard. Access is protected by a 4-digit
PIN with rate limiting, and it is meant for **trusted private networks only**. The plain HTTP endpoint is unencrypted;
the HTTPS endpoint uses a self-signed certificate. Do not run it on public or untrusted Wi-Fi, and stop it when idle.

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/Wombaflow/mac-remote/security/advisories/new)
rather than a public issue. Include steps to reproduce and the affected version (`python3 mac_remote.py --version`).
You can expect an initial response within a few days.

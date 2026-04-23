# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take the security of traktor seriously. If you have discovered a security vulnerability, we appreciate your help in disclosing it to us in a responsible manner.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please send an email to the repository maintainers with the following information:

1. **Description**: A clear description of the vulnerability
2. **Steps to Reproduce**: Detailed steps to reproduce the issue
3. **Impact**: What could an attacker achieve by exploiting this vulnerability?
4. **Affected Versions**: Which versions of traktor are affected?
5. **Suggested Fix**: If you have one, please include a suggested fix

### Response Timeline

- **Acknowledgment**: Within 48 hours of receiving your report
- **Initial Assessment**: Within 5 business days
- **Fix Timeline**: Depends on severity and complexity, but we aim to:
  - Critical: Within 7 days
  - High: Within 14 days
  - Medium/Low: Within 30 days

### Security Best Practices for Users

- **Never commit credentials**: Do not commit `.env`, token files, or cache files to version control
- **Protect tokens**: Treat Trakt tokens and Plex tokens as secrets. Rotate them if exposed
- **Use least privilege**: Create a dedicated Trakt API application with minimal permissions
- **Network security**: Ensure your Plex server is secured and not exposed unnecessarily
- **Container security**: When using Docker, mount `.env` read-only and don't commit runtime state

### Security Considerations

This tool handles sensitive authentication data:

- Trakt OAuth tokens are stored in `~/.traktor_trakt_token.json` with 0600 permissions
- Plex tokens are read from environment variables or `.env` files
- Tokens are logged at debug level only (truncated)
- No analytics or telemetry is collected

### Known Limitations

- Token files are stored locally with filesystem-level permissions
- No built-in encryption of stored tokens (relies on OS-level security)
- API requests are made over HTTPS but certificate validation depends on the `requests` library

## Acknowledgments

We will publicly acknowledge security researchers who report valid vulnerabilities (with their permission) in our release notes.

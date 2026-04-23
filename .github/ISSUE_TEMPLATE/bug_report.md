---
name: Bug report
about: Create a report to help us improve traktor
title: '[BUG] '
labels: bug
description: Report something that's not working correctly
---

## Bug Description

A clear and concise description of what the bug is.

## To Reproduce

Steps to reproduce the behavior:
1. Run command '...'
2. With configuration '...'
3. See error

## Expected Behavior

A clear and concise description of what you expected to happen.

## Actual Behavior

What actually happened instead.

## Environment

- **OS**: [e.g. Ubuntu 22.04, macOS 14, Windows 11]
- **Python version**: [e.g. 3.12]
- **traktor version**: [e.g. 1.0.0]
- **Installation method**: [e.g. uv, pip, Docker]

## Configuration

```env
# Redact sensitive values with ***
TRAKT_CLIENT_ID=***
TRAKT_CLIENT_SECRET=***
PLEX_URL=http://...
PLEX_TOKEN=***
```

## Logs

```
# Include relevant log output from ~/.traktor/traktor.log
# Redact tokens and sensitive information
```

## Additional Context

- Does the issue happen consistently or intermittently?
- Does refreshing the cache (`--refresh-cache`) help?
- Does re-authenticating (`--force-auth`) help?
- Are you running in Docker or locally?

## Possible Solution

If you have suggestions on how to fix the bug, please describe them here.

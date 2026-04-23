# Mission-Critical Runbook

This runbook provides operational guidance for running traktor in production environments with high reliability requirements.

## Overview

traktor includes enterprise-grade resilience features for mission-critical deployments:

- **Circuit Breakers**: Prevent cascading failures when APIs are down
- **Health Monitoring**: Continuous health checks with status reporting
- **Automated Backups**: Pre-sync and scheduled backup capabilities
- **Integrity Verification**: Detect corruption before it causes failures
- **Rate Limiting**: Enforced API rate limits with automatic throttling
- **Exponential Backoff**: Intelligent retry logic for transient failures

## Quick Reference

### Health Status
```bash
# Check system health
uv run traktor --health-check

# Check circuit breaker status
uv run traktor --circuit-status

# Verify data integrity
uv run traktor --integrity-check
```

### Backup Operations
```bash
# Create manual backup
uv run traktor --backup

# List available backups
uv run traktor --backup-list

# Restore from backup
uv run traktor --backup-restore /path/to/backup
```

### Safe Sync Operations
```bash
# Always backup before major operations
uv run traktor --backup && uv run traktor --sync-watched --dry-run

# Run with verbose logging for troubleshooting
uv run traktor -v 2>&1 | tee traktor-$(date +%Y%m%d-%H%M%S).log
```

## Resilience Architecture

### Circuit Breakers

Two circuit breakers protect API calls:

| Circuit | Threshold | Cooldown | Purpose |
|---------|-----------|----------|---------|
| `trakt_api` | 5 failures | 60s | Trakt API protection |
| `plex_api` | 3 failures | 30s | Plex API protection |

**States:**
- **CLOSED** (🟢): Normal operation, requests pass through
- **OPEN** (🔴): Failing fast, rejecting requests
- **HALF_OPEN** (🟡): Testing recovery with limited traffic

**When OPEN:**
- Sync operations will fail fast with clear error messages
- No API calls are attempted (preventing further load on failing service)
- Automatic recovery after cooldown period

### Health Checks

Registered components:
- `cache`: Cache directory exists and is accessible
- `config`: Configuration file is valid JSON (if exists)

Status levels:
- **HEALTHY** (✅): All checks passing
- **DEGRADED** (⚠️): 2-4 consecutive failures
- **UNHEALTHY** (❌): 5+ consecutive failures

### Backup System

**Automatic backups** are created:
- Before every sync operation (if `BACKUP_BEFORE_SYNC=true`)
- On schedule via cron/systemd timer
- Manually via `--backup` flag

**Backup contents:**
- `config`: `~/.traktor_config.json`
- `token`: `~/.traktor_trakt_token.json`
- `cache`: `~/.traktor_cache/` directory

**Retention:** Default 10 backups (configurable)

### Integrity Checks

Verifies:
- Config file: Valid JSON structure
- Token file: Valid JSON with required keys (`access_token`, `refresh_token`)
- Cache files: All JSON files parseable

## Operational Procedures

### Daily Operations

1. **Morning health check** (automated via cron):
   ```bash
   uv run traktor --health-check || echo "ALERT: traktor health check failed" | mail -s "traktor alert" ops@example.com
   ```

2. **Regular sync** (automated):
   ```bash
   uv run traktor --sync-watched --sync-collection
   ```

3. **Log review**:
   ```bash
   tail -n 100 ~/.traktor/traktor.log | grep -E "ERROR|CRITICAL|WARNING"
   ```

### Incident Response

#### Scenario: Trakt API Circuit Breaker Open

**Symptoms:**
- Circuit status shows 🔴 OPEN
- Error: "Trakt API unavailable (circuit breaker open)"

**Response:**
1. Check Trakt status: https://status.trakt.tv
2. Wait cooldown period (60s) or restart to force check
3. If persistent, verify credentials with `--diagnose`

#### Scenario: Cache Corruption

**Symptoms:**
- `--integrity-check` shows corrupt cache files
- Unexpected errors during sync

**Response:**
1. Create backup: `uv run traktor --backup`
2. Clear cache: `rm -rf ~/.traktor_cache/*`
3. Rebuild: `uv run traktor --refresh-cache`

#### Scenario: Sync Failure Mid-Operation

**Response:**
1. Check logs for error details
2. Run health check: `uv run traktor --health-check`
3. If needed, restore from backup: `uv run traktor --backup-list` then `--backup-restore`
4. Retry with `--dry-run` first: `uv run traktor --sync-watched --dry-run`

#### Scenario: Token Authentication Failure

**Symptoms:**
- 401 errors from Trakt API
- Token refresh failures in logs

**Response:**
1. Verify with `--diagnose`
2. Force re-authentication: `uv run traktor --force-auth`

### Recovery Procedures

#### Full State Recovery

If traktor is completely broken:

1. **Stop any running syncs** (Ctrl+C or kill process)

2. **List available backups:**
   ```bash
   uv run traktor --backup-list
   ```

3. **Restore most recent backup:**
   ```bash
   uv run traktor --backup-restore ~/.traktor_backups/traktor_backup_YYYYMMDD_HHMMSS_manual
   ```

4. **Verify integrity:**
   ```bash
   uv run traktor --integrity-check
   ```

5. **Test with dry-run:**
   ```bash
   uv run traktor --sync-watched --dry-run
   ```

6. **Resume normal operations:**
   ```bash
   uv run traktor
   ```

#### Partial Cache Recovery

If only cache is corrupted (config and tokens are fine):

1. **Backup current state:**
   ```bash
   uv run traktor --backup
   ```

2. **Clear only cache:**
   ```bash
   rm -rf ~/.traktor_cache/*
   ```

3. **Rebuild with refresh:**
   ```bash
   uv run traktor --refresh-cache
   ```

## Monitoring & Alerting

### Recommended Metrics

Track these via your monitoring system (Prometheus, DataDog, etc.):

```bash
# Health status (0=healthy, 1=degraded, 2=unhealthy)
uv run traktor --health-check; echo "Exit code: $?"

# Circuit breaker states (check for "open" state)
uv run traktor --circuit-status | grep -c "open"

# Integrity status (0=pass, 1=fail)
uv run traktor --integrity-check; echo "Exit code: $?"
```

### Log-Based Alerts

Configure log monitoring to alert on:

```
CRITICAL  # Unhandled exceptions
"circuit breaker OPEN"  # API failures
"Backup verification failed"  # Backup corruption
"Integrity check failed"  # Data corruption
```

### Docker Health Check

Add to docker-compose.yml for container orchestration:

```yaml
healthcheck:
  test: ["CMD", "uv", "run", "traktor", "--health-check"]
  interval: 1m
  timeout: 10s
  retries: 3
  start_period: 30s
```

## Configuration for Production

### Environment Variables

```bash
# Enable automatic pre-sync backups
export TRAKTOR_BACKUP_BEFORE_SYNC=true

# Increase backup retention
export TRAKTOR_MAX_BACKUPS=20

# Custom backup directory
export TRAKTOR_BACKUP_DIR=/var/backups/traktor

# Circuit breaker tuning (adjust based on your environment)
export TRAKTOR_TRAKT_FAILURE_THRESHOLD=3
export TRAKTOR_TRAKT_COOLDOWN_SECONDS=120
export TRAKTOR_PLEX_FAILURE_THRESHOLD=5
export TRAKTOR_PLEX_COOLDOWN_SECONDS=60
```

### Cron Schedule Example

```cron
# Health check every 5 minutes
*/5 * * * * cd /path/to/traktor && uv run traktor --health-check >/dev/null 2>&1 || echo "traktor health check failed" | mail -s "ALERT" ops@example.com

# Full sync every 6 hours
0 */6 * * * cd /path/to/traktor && uv run traktor --sync-watched --sync-collection --sync-progress 2>&1 | logger -t traktor

# Weekly backup rotation (keeps manual + recent automatic)
0 2 * * 0 cd /path/to/traktor && find ~/.traktor_backups -name "traktor_backup_*" -mtime +30 -delete
```

### Systemd Service

```ini
# /etc/systemd/system/traktor.service
[Unit]
Description=Traktor Sync Service
After=network.target

[Service]
Type=oneshot
User=traktor
WorkingDirectory=/opt/traktor
ExecStart=/usr/local/bin/uv run traktor --sync-watched
Environment="TRAKTOR_BACKUP_BEFORE_SYNC=true"

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/traktor.timer
[Unit]
Description=Run traktor every 6 hours

[Timer]
OnCalendar=*:0/6
Persistent=true

[Install]
WantedBy=timers.target
```

## Troubleshooting Matrix

| Symptom | Check | Action |
|---------|-------|--------|
| Sync fails immediately | `--circuit-status` | Wait for cooldown or restart |
| Slow sync performance | `--health-check` | Check cache, may need `--refresh-cache` |
| Missing items in playlists | `--integrity-check` | Check cache, verify Plex library |
| Auth failures | `--diagnose` | `--force-auth` to re-authenticate |
| Playlists not updating | Check Plex token owner | Verify server owner token vs managed user |
| High memory usage | Check worker count | Reduce `--workers` or enable swap |
| Disk space issues | Check backup directory | Reduce `TRAKTOR_MAX_BACKUPS` or move backups |

## Security Considerations

### Backup Security

- Backups contain OAuth tokens and Plex credentials
- Store backups in encrypted storage (LUKS, EBS encryption, etc.)
- Limit backup directory permissions: `chmod 700 ~/.traktor_backups`

### Token Rotation

Tokens are backed up automatically. After token rotation:
1. Create new backup: `uv run traktor --backup`
2. Remove old backups containing expired tokens

## Performance Tuning

### For Large Libraries (10,000+ items)

```bash
# Increase workers but stay within API limits
uv run traktor -w 12

# Use delta sync (skips unchanged items)
# Enabled by default after first run

# Schedule during low-traffic hours to avoid Plex load
```

### For Frequent Syncs

```bash
# Every 30 minutes for active users
*/30 * * * * uv run traktor --sync-watched

# Disable full playlist sync (expensive) more frequently
*/30 * * * * uv run traktor --sync-watched --list-source liked
```

## Contact & Escalation

**Level 1 - Self-Service:**
- Run diagnostics: `uv run traktor --diagnose`
- Check runbook (this document)
- Review logs: `~/.traktor/traktor.log`

**Level 2 - Advanced Troubleshooting:**
- Restore from backup
- Force cache refresh
- Enable verbose logging and analyze

**Level 3 - Code Issues:**
- GitHub Issues: https://github.com/SwordfishTrumpet/traktor/issues
- Include: logs, `--diagnose` output, reproduction steps

---

**Document Version:** 1.0.0  
**Last Updated:** 2026-04-19  
**traktor Version:** 1.0.0+

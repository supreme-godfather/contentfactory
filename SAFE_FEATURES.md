# Safe capabilities adopted from the reference engine

The quarantined n8n collection contains useful automation ideas, but it is not
imported. The clean factory adopts only patterns that fit this product and can
be secured.

## Included or planned

- Durable queued jobs and explicit lifecycle states
- Scheduling, pause/resume, bounded retries, and checkpoint/resume
- Worker heartbeats and expiring leases when RDP is offline
- Telegram notifications and inline approval buttons
- Platform-specific publication adapters
- Idempotent publication records and upload reconciliation
- Trend discovery with originality and risk scoring
- Analytics snapshots and performance recommendations
- Brand/channel profiles, account quotas, and configurable niches
- Quality gates, asset provenance, disclosure, and audit records
- Health checks, storage limits, and operator-visible errors
- Safe HTTP integrations with timeouts and explicit allowlists

## Explicitly excluded

- Exporting or decrypting credentials
- Shell/PowerShell execution from workflows or Telegram
- Arbitrary SQL, filesystem paths, or URL fetching from user input
- Unauthenticated public webhooks
- Browser cookies or private Instagram upload methods
- Copying creators' scripts, videos, thumbnails, audio, or distinctive style
- Silent paid-service fallback
- Importing the reference workflow JSON files into production

The goal is not to reproduce every generic workflow. It is to keep the useful
orchestration patterns while maintaining a small, auditable content factory.

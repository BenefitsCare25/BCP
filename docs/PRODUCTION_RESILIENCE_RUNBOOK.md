# Inspro production resilience and ransomware-recovery design

Last reviewed: 2026-08-25
Production region: Azure `southeastasia` (Singapore) only

## Recovery record: 2026-08-25

Azure Resource Health declared `inspro-prod-pg` unavailable at 2026-08-25 11:09:31 SGT with a platform-initiated, unplanned outage. A restart was accepted and subsequently failed while the resource remained unavailable.

The approved recovery was a latest-point-in-time restore to `inspro-prod-pg-recovery-20260825`:

- Location: Southeast Asia, availability zone 1.
- SKU: `Standard_B2s`, Burstable; 64 GiB storage.
- Backup retention: 35 days; geo-redundant backup disabled.
- Source preserved: `inspro-prod-pg` in availability zone 2.
- Restored server reached `Ready`; `is_db_alive` reported `1`.
- Private endpoint `inspro-prod-pg-recovery-20260825-pe` was approved and registered as `10.20.2.7` in the Singapore VNet private DNS zone.
- The claim-review worker returned `200 /readyz` against the restored database before API cutover.
- Source storage at the last pre-outage metric was 6,207,057,920 bytes; restored storage was 6,156,353,536 bytes. The approximately 0.8% difference is consistent with transient/system storage rather than missing application schemas.
- The API returned `200 /readiness` after cutover with PostgreSQL and Redis both healthy.
- The `Standard_D2ds_v5` and zone-redundant HA upgrade was explicitly deferred. The single-server infrastructure-failure risk remains accepted until that decision changes.

Do not delete the source server until the recovered production server has remained stable for at least 48–72 hours, recent transactions have been reconciled, and deletion receives explicit approval.

## Purpose and service objectives

This document defines the target setup for keeping Inspro available during ordinary infrastructure failures and recovering safely from ransomware or destructive account compromise.

There is no honest zero-downtime guarantee for a ransomware incident. If database integrity or production identities might be compromised, transaction processing must be isolated until a clean recovery point is validated. The service should keep a static status and support experience online where safe, but it must not automatically fail over to a database that may contain the same malicious changes.

Target objectives:

| Scenario | Availability objective | Data objective |
| --- | --- | --- |
| App instance failure | Automatic routing to a healthy instance; target under 5 minutes | No database data loss |
| PostgreSQL host or availability-zone failure | Zone-redundant HA failover | Zero loss of committed transactions is the Azure HA design objective |
| Bad release | Blue-green/slot rollback; target under 15 minutes | Backward-compatible migrations prevent rollback data loss |
| Ransomware or malicious logical database change | Restricted-mode website while clean recovery is performed; full service RTO target at most 4 hours | PITR target RPO at most 5 minutes, measured from the last verified clean point |
| Entire Singapore Azure region unavailable | No in-region failover is possible | Business acceptance is required because data and workloads may not leave Singapore |

RPO is the acceptable data-loss window. RTO is the time allowed to restore full service. Attack detection and forensic validation can make the effective ransomware RPO or RTO longer than the platform backup figures.

## Current baseline

The repository currently defines:

- All production resources in `southeastasia`.
- PostgreSQL Flexible Server 16 on `Standard_B2s`, Burstable, 64 GiB, with 35-day point-in-time retention.
- Geo-redundant PostgreSQL backup disabled so data is not copied to the paired Hong Kong region.
- One B2 App Service Plan instance with separate API and claim-review worker web apps.
- Private endpoints for PostgreSQL, Redis and Blob Storage; App Service VNet integration.
- Key Vault soft delete and purge protection, managed identities, diagnostics and bounded database connection/pool timeouts.
- Separate `/health` liveness and `/readiness` dependency probes.
- Singapore synthetic probes and alerts to the configured operations recipient and `huien@inspro.com.sg`.

The largest remaining resilience gaps are PostgreSQL HA being disabled, a single App Service Plan instance, in-place releases without deployment slots, and no independently administered immutable PostgreSQL backup vault or rehearsed clean-room restore.

## Target Singapore-only architecture

```text
Users
  |
  v
Singapore ingress/WAF
  |
  +--> API instance A -----+
  |                        +--> private endpoint --> PostgreSQL primary (AZ 1)
  +--> API instance B -----+                         | synchronous HA
                                                    v
                                              standby (AZ 2/3)

Singapore monitoring --> alerts + incident response

PostgreSQL PITR (35 days) --> routine restore to a new Singapore server
               |
               +--> immutable Backup Vault in a separate backup subscription
                    location: Southeast Asia; locked retention; separate admins

Pre-provisioned clean-room network and app capacity in Southeast Asia
  +--> receives a validated restore only during a declared cyber incident
```

All resources that contain application data, documents, backups, logs or secrets must use `southeastasia`. Do not enable PostgreSQL geo-redundant backup, storage GRS/GZRS/RA-GRS/RA-GZRS, cross-region restore, or a recovery vault outside Singapore. Azure global control-plane services and email delivery metadata require a separate residency assessment; they must not receive member or claims payloads.

## Required implementation

### 1. PostgreSQL infrastructure availability

Change production parameters to:

```json
"postgresSku": { "value": "Standard_D2ds_v5" },
"postgresTier": { "value": "GeneralPurpose" },
"postgresHighAvailability": { "value": true }
```

Keep:

```json
"location": { "value": "southeastasia" },
"postgresBackupRetentionDays": { "value": 35 },
"postgresGeoRedundantBackup": { "value": false }
```

Before applying the change:

1. Confirm `Standard_D2ds_v5` and zone-redundant HA capacity in Southeast Asia.
2. Capture current server configuration, metrics and a successful PITR test.
3. Schedule a maintenance window because the tier/HA transition itself can interrupt connections.
4. Confirm application connection retries are bounded and safe for idempotent operations.
5. Deploy through Bicep and verify the primary and standby are in different availability zones.
6. Trigger a planned failover outside business hours and record actual RTO.

HA protects platform and zone failures. Synchronous replication also copies malicious SQL changes, so HA is not a ransomware backup.

### 2. Multi-instance application and safe deployment

Move production from Basic B2 to a zone-capable Premium App Service plan supported in Southeast Asia. Configure at least two instances; three are preferred when zone redundancy is enabled. Recalculate PostgreSQL connection ceilings before scaling because each API and worker process owns a connection pool.

Add a staging deployment slot and use this release sequence:

1. Build an immutable image tagged by Git commit SHA.
2. Deploy to the staging slot.
3. Run `/health`, `/readiness`, migration compatibility and critical smoke tests.
4. Swap the slot into production.
5. Monitor 5xx, latency, readiness and worker health.
6. Swap back immediately if the release gate fails.

Database migrations must be backward compatible using expand/migrate/contract. Destructive schema cleanup is a later, separately approved release.

### 3. Ransomware-resistant backup boundary

Create a dedicated production-backup Azure subscription with a separate emergency administration group. In that subscription, create a Backup Vault in `southeastasia` and protect PostgreSQL Flexible Server with long-term retention.

Required controls:

- Vault immutability enabled, validated, then locked. Locking is irreversible.
- Soft delete enabled and set to the approved retention period.
- Multi-user authorization/Resource Guard for critical backup operations where supported.
- Backup operators separated from workload owners; no application identity receives backup delete or restore permissions.
- Privileged Identity Management, phishing-resistant MFA and approval-based activation for backup administrators.
- Diagnostic logs sent to the Southeast Asia Log Analytics workspace without claim/member payloads.
- Resource locks on the PostgreSQL server, Key Vault, Backup Vault and recovery infrastructure.
- A documented retention decision covering operational, legal, PDPA and storage-cost requirements before locking immutability.

The normal 35-day PostgreSQL PITR service remains the fast recovery path. The independently administered immutable vault is the clean recovery path if production administrators or normal backups are untrusted. Verify the current PostgreSQL Backup Vault restore workflow and limitations in a non-production rehearsal before relying on it.

### 4. Clean-room recovery environment

Maintain a separate recovery resource group or subscription in `southeastasia`, provisioned from reviewed Bicep. Pre-create the VNet, private DNS, subnets, Key Vault, App Service capacity, monitoring and role assignments. Do not give the production app identity access to the recovery environment.

The clean room must have:

- A known-clean image digest and dependency manifest.
- Fresh managed identities and newly rotated secrets.
- No automatic trust of production automation or administrator sessions.
- A quarantine storage location for restored data validation.
- Integrity checks for tenant schemas, migrations, row counts, audit history and high-value transactions.
- An explicit human approval gate before DNS/traffic cutover.

Do not continuously replicate logical database writes into this recovery database. A delayed or immutable recovery point is necessary because an always-current replica can contain the attack.

### 5. Restricted-mode customer experience

Add an operator-controlled, audited incident-mode feature flag. It should:

- Keep a static landing page, incident status, contact information and non-sensitive help content available.
- Disable sign-in and database-dependent reads when confidentiality or integrity is uncertain.
- Disable mutations by default.
- Permit queued submissions only after a threat review confirms that the application and queue identities are clean; clearly tell users that processing is delayed.
- Never display cached personal, health or claims data after a database-integrity declaration.

This provides continuity of communication, not full zero-downtime transaction processing.

### 6. Prevention and detection controls

- Keep PostgreSQL on private networking with an empty public allowlist. Move CI migrations onto a self-hosted runner or deployment job inside the VNet, then disable public network access completely.
- Replace long-lived database passwords with managed identity/Microsoft Entra authentication where application-driver support and token refresh have been validated.
- Give the API and worker separate least-privilege database roles; neither should use the PostgreSQL administrator role.
- Require MFA, Conditional Access and PIM for Azure administrators. Maintain two monitored break-glass accounts.
- Enable Microsoft Defender for Cloud plans appropriate to App Service, Storage, Key Vault and databases.
- Add alerts for PostgreSQL server/configuration deletion, firewall changes, role grants, mass table changes, unusual sign-ins, Key Vault access spikes, backup-policy changes and disabled monitoring.
- Retain security/audit logs for at least 90 days in Singapore and make the archive tamper-resistant.
- Run secret scanning, SAST, dependency scanning and IaC scanning in CI. Block critical/high unresolved findings from production.

### 7. Alert routing

The existing Azure Monitor action group must continue emailing `huien@inspro.com.sg`. Add a second independent channel such as SMS, Teams or an on-call service, because ransomware can disrupt corporate email.

Severity-0 alerts should include:

- Public liveness failure.
- Dependency readiness failure.
- PostgreSQL resource unavailable/degraded.
- Suspected ransomware or destructive database activity.
- Backup failure, disabled protection, retention reduction or attempted recovery-point deletion.
- Key Vault purge/delete attempt or anomalous secret access.

Every alert needs an owner, acknowledgement target, escalation path and runbook link. Test the action group quarterly.

## Ransomware incident runbook

### Declaration and containment

1. Declare a security incident; record the time and incident commander.
2. Put the portal into restricted mode. Do not automatically fail over the database.
3. Preserve logs and evidence. Do not restart or delete suspected compromised resources before evidence capture unless continued operation increases harm.
4. Revoke suspected sessions and credentials, disable compromised identities and block malicious network paths.
5. Separate the clean recovery administrators from the potentially compromised production administration path.

### Select and restore a clean point

1. Establish the earliest known indicator of compromise.
2. Select a recovery point before that time, allowing for attacker dwell time.
3. Restore to a **new** PostgreSQL server in `southeastasia`; never overwrite the source server.
4. Attach the restore only to the isolated clean-room network.
5. Scan restored data and validate schema versions, tenant boundaries, row counts, audit trails and high-risk business records.

### Rebuild and cut over

1. Deploy the last known-clean signed application image into the clean room.
2. Rotate PostgreSQL, Key Vault, Entra application, CI/CD and integration credentials.
3. Run migrations only after confirming their commit and artifact provenance.
4. Verify `/health`, `/readiness`, authentication and critical read/write journeys using non-production test records.
5. Obtain incident-commander and business-owner approval.
6. Route production traffic to the clean environment and monitor continuously.
7. Keep the compromised environment isolated for forensic analysis.

### Recovery validation and notification

- Record achieved RPO/RTO and any known missing transactions.
- Reconcile queued and out-of-band business transactions.
- Assess whether the incident is a notifiable data breach under Singapore PDPA and any sector rules.
- Complete root-cause analysis, lessons learned and control remediation before closing the incident.

## Routine PostgreSQL outage runbook

Use this only when evidence shows a platform availability problem and there is no indicator of compromise.

1. Check public `/health` and `/readiness` independently.
2. Inspect PostgreSQL resource health, server state, activity logs and Azure Service Health.
3. Confirm the app can resolve and connect to the PostgreSQL private endpoint.
4. If the server is stopped, start it. If it is stuck in a transient state, open an Azure support case before repeated restarts.
5. If HA is enabled and the primary is unhealthy, use a planned/forced failover only according to Azure guidance and after confirming the server state.
6. Restart the application only if its connection pool does not recover after PostgreSQL is healthy.
7. Verify `/health`, `/readiness`, homepage, sign-in and a safe database read.
8. Record the incident timeline and alert delivery.

A server restart is not a ransomware recovery method and must not be used to erase or reinitialize data.

## Implementation order and acceptance gates

1. **Immediate:** restore current production availability without deleting data; preserve incident evidence.
2. **Deferred by current decision:** keep PostgreSQL on `Standard_B2s` without HA. Revisit General Purpose plus zone HA as a separately approved resilience project and run a failover exercise if approved.
3. **Week 1–2:** create and lock the Singapore immutable backup boundary after a successful test restore.
4. **Week 2:** upgrade App Service, configure multiple instances and deployment slots, then load/failover test connection limits.
5. **Week 2–3:** implement restricted mode and the isolated clean-room deployment.
6. **Quarterly:** test app failover, PITR, immutable restore, alert delivery and the complete cyber-recovery cutover.

Production resilience is accepted only when evidence demonstrates:

- All data-bearing resources and recovery points remain in Southeast Asia.
- PostgreSQL reports zone-redundant HA healthy.
- Two or more healthy application instances serve traffic.
- A clean PITR restore and an immutable-backup recovery test have completed successfully.
- Alert delivery reaches `huien@inspro.com.sg` and the independent secondary channel.
- The ransomware exercise meets the approved RTO/RPO or records a funded remediation plan.

## Authoritative references

- [Azure PostgreSQL high availability](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-high-availability)
- [Azure PostgreSQL backup and point-in-time restore](https://learn.microsoft.com/azure/postgresql/backup-restore/concepts-backup-restore)
- [Azure ransomware-resilient backup architecture](https://learn.microsoft.com/azure/architecture/security/ransomware-resilient-backup-architecture/)
- [Azure Backup immutable vault](https://learn.microsoft.com/azure/backup/backup-azure-immutable-vault-concept)
- [Azure Backup ransomware protection FAQ](https://learn.microsoft.com/azure/backup/protect-backups-from-ransomware-faq)
- [Azure PostgreSQL private networking](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-networking-private)

# Project guidance

## One shared local database

Read [the local development memory](docs/LOCAL_DEVELOPMENT_MEMORY.md) before starting local services, importing data or preparing browser reviews. The only persistent working database is `backend/inspro.db`; retained documents use `backend/var/uploads/`. All local broker, employee, HR and manual review sessions must use these same stores.

Start both services from the project root with `uv run --directory backend python -m scripts.local_dev`. Use `http://localhost:5173` and API port 8000. Do not create review/demo database copies, run old `tmp/start-*-review.py` launchers, replace the working database with seed data, or switch the frontend to another API/database. Use labelled additive fixtures in the shared database. Automated destructive tests may use disposable databases outside the workspace; they must never target the working database. No local backups are requested; do not create them without a new user request.

## Employee and HR portal UI/UX

For employee/member and HR portal interface work, read [the scoped project memory](docs/EMPLOYEE_HR_PORTAL_DESIGN_MEMORY.md) and follow its requirements and page review checklist. The current Home/enrolment implementation and these user-approved requirements take precedence over older portal directions in docs/archive/PORTAL_DESIGN_2026-09-25.md.

This theme applies only to employee and HR portals, including their sign-in, security and shared components when rendered there. Do not change broker/admin interface styling through unscoped shared component changes.

Work page by page. Finish each page's layout, content, colour, hover/focus, responsive and functional review before advancing. Record evidence and unresolved gaps in the memory's review register. Use labelled, additive, local-only review fixtures where data is missing; never overwrite employee identity, real coverage, elections or credentials.

## Broker portal permissions

Follow the broker-only authorization requirements in [the broker UI guide](docs/BROKER_UI.md). Only `system_admin` may see or manage the Users section, including invitations, renaming, role/status changes and revocation. Only `system_admin` may use saved-data Remove, Delete, Clear all, Unlink or destructive reset controls throughout the broker portal. Enforce these permissions in both the interface and API. Keep company creation/editing and nonpersistent search/filter clearing under their existing permissions. This policy does not change the employee or HR portal theme or self-service permissions.

## Local Codex MCP profiles

Before changing or troubleshooting MCP servers, read C:\Users\huien\CODEX_MCP_RUNBOOK.md. Preserve isolation between codex1 and codex2; do not copy authentication files or store MCP secrets in TOML. For secondary-profile changes use codex2 mcp, and keep C:\users\huien project trust untrusted.

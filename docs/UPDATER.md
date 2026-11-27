# Settings › Software update — design (release 0.67.0, moved to the main Settings in 0.67.1; DEC-111)

## Goal
Install a newer COREI-OS-MINI release from the workstation, safely: no SSH for routine updates, never an update that
interrupts an order whose outcome is unknown, always a way back, everything audited.

## Architecture

```
Browser (admin) ──▶ workstation (no root, read-only system)            root updater (systemd, fixed copy)
  Settings › Update      update.py: state, safety checks,     inbox/request.json      scripts/corei-updater.py
                          audit + alert, writes request  ───────────────────────▶   installed in /usr/local/lib/corei-os-mini
                                                         ◀───────────────────────   outbox/available.json, status.json, update-<id>.log
```

| Piece | Where | Rights |
|---|---|---|
| Request folder | `/var/lib/corei-os-mini/update/inbox` | owned by the workstation user, 0700; the only path the service may write here |
| Result folder | `/var/lib/corei-os-mini/update/outbox` | root-owned, group-readable by the workstation user (0750) |
| Updater | `/usr/local/lib/corei-os-mini/corei-updater.py` | root-owned copy, so the workstation cannot change it |
| Units | `corei-os-mini-updater.service` (oneshot), `.path` (starts it when a request appears), `.timer` (check every 6 h) | installed by `scripts/deploy-workstation.py` |

## What an update does (updater, in order)
1. **Check GitHub again** as the repository user: `git fetch origin main`; current/latest commit and VERSION; commits in between; new
   migrations; GitHub check status.
2. **Refuse** (nothing changes) if: the request is older than 15 minutes, a symbolic link, too large or not owned by the user;
   the target is not exactly the current tip of `origin/main`; the checkout is not on `main`, has local changes or local
   commits; GitHub checks failed; checks are pending/unknown without the operator's acknowledgement.
3. **Back up the database** (`corei_os_mini.md.jobs backup`, to `COREI_BACKUP_DIR`). Required when the update adds migrations,
   unless the operator acknowledged "update without a backup".
4. **Fast-forward** to the target (`git merge --ff-only`).
5. **Build** (`scripts/install-workstation.sh`), **migrate** (`configure-workstation-database.py`), **deploy**
   (`deploy-workstation.py`: restarts and health-checks the service; restores the unit and Caddy itself on failure).
6. **Verify** the service reports the target commit.
7. On any failure after step 4: **roll back the code** (`git reset --hard <previous>`), rebuild and redeploy. Database changes
   stay (migrations are additive). If the rollback also fails, the status says so and lists the manual commands.

## What the workstation checks before it accepts a request
- Administrator; password asked again (re-authentication); reason; typed **UPDATE**; the exact latest commit shown on screen.
- **Blocks:** updater not installed; another update waiting or running; not checked yet / nothing newer; orders in
  SUBMITTED, CANCEL_PENDING or UNKNOWN (a restart must not interrupt them); last check failed; checkout not fast-forwardable;
  GitHub checks failed.
- **Must be acknowledged:** the 1–3 minute restart (prices, collectors, order handling); LIVE order sending ON; Production mode;
  active strategy deployments; GitHub checks not passed; database changes without backups.
- Audit `UPDATE_REQUESTED` in the same transaction as the safety re-check, with an alert; each outcome (`UPDATE_SUCCEEDED`,
  `_ROLLED_BACK`, `_FAILED`, `_REFUSED`) is audited once with an alert.

## Options and controls on the screen
Where: **Settings › Software update** (main Settings), **Help › Check for updates…**, **Settings › About › Check for updates…**.
Running vs GitHub version · Check now · automatic check every 6 h · what's new (commits, database changes, GitHub checks) ·
blockers · Update now (acknowledgements, reason, UPDATE, password) · live steps and log · reload when the new version runs ·
history · manual commands.

## First installation
The updater is installed by a normal deploy, so the release that introduces it is installed by hand once:
`cd ~/corei-os-mini && git fetch origin && git checkout main && git merge --ff-only origin/main && bash scripts/deploy-workstation.sh`.

## Not in this release (proposals)
Scheduled update window · "roll back to the previous release" button · automatic database restore · release channels
(tags instead of `main`) · signed release tags · update notifications in the top bar.

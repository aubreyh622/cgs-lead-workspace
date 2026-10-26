# Local operations handoff

This is a single-user localhost prototype. Maintained engineering material is English.
The phase gate remains in force: P5 implementation does not authorize further development,
external deployment, live provider spending or client outreach.

## Entry points and storage

| Purpose | Entry point | Port | Data |
| --- | --- | --- | --- |
| Main integrated workspace | Start CGS Workspace.cmd / `python -B workspace.py serve` | 8766 | `runtime/research.sqlite3`, `runtime/qualification.sqlite3` |
| Configuration check, no provider calls | Check Workspace.cmd / `python -B workspace.py check` | None | Opens main stores; reports readiness without secrets |
| Explicit offline rehearsal | Start Offline Demo.cmd / `python -B demo.py` | 8767 | `runtime/offline-demo/runtime/research.sqlite3`, `runtime/offline-demo/runtime/qualification.sqlite3` |
| Preparation evidence only | `python -B demo.py --prepare-only --workspace-dir runtime/new-demo-session` | Ephemeral localhost during preparation | New isolated session and `pilot-evidence.json` |

Run commands from the project folder with Python 3.12 and `requirements.lock` dependencies.
On this machine the launchers reuse `.local-python`; on another machine run `Setup.cmd`.
Fresh environment installation and client cloud deployment have not been verified.

Main provider settings come from the server environment / project `.env`:
Gemini + Tavily for research, Gemini for feedback analysis, and OpenAI for qualification.
Restart after changes. A configured flag means values are present, not that an account works.
Keep keys in local secret configuration; never include them in reports, screenshots or archives.

The offline entry point does not load `.env`; it replaces all three provider paths, locks
qualification to demo rules and permits only its two named fictional companies. A main live
provider failure is an error and cannot turn the main workspace into this demo.

## Routine use and recovery

- Keep the server terminal open. Stop with Ctrl+C; do not close databases mid-operation.
- One company per research task; one active task at a time. Limits and the direction version
  are frozen on creation. Current scope uses a human-supplied company, not broad autonomous discovery.
- Stop research at a checkpoint; explicitly resume saved paused/failed work if appropriate.
  Resume retains completed steps, the original direction and consumed budgets.
- After an abrupt termination, wait for the 120-second execution lease to expire before explicit
  resume. There is no automatic paid retry or automatic recurring research.
- Provider errors: inspect sanitized progress, check local configuration and account status,
  then retry explicitly. Qualification suggestions and direction proposals require human review.
- If the UI appears stale, use Refresh / Load manager context, or reload. Save operations use
  request IDs where documented; a network retry must keep the original intent. A stale revision
  requires reloading before making a new decision.
- An accepted opportunity is frozen. Correct downstream facts through the case workflow; create
  a distinct new opportunity for a different team/problem rather than overwriting the snapshot.
- Same-company warnings are advisory. Record the actual relationship/duplicate check in acceptance.
- A returned handoff creates no case. Revise the opportunity feedback and resubmit for a new decision.
- Reset qualification samples is blocked once any research handoff is accepted.
- Reopening a completed offline session preserves interactive edits. It does not rerun the seed
  pilot or regenerate historical preparation evidence. For a fresh run, choose a new empty directory.
- If preparation stops before completion, preserve that directory for diagnosis and choose another
  empty demo directory. Do not delete or overwrite an unrelated directory to recover.
- A port conflict is resolved by stopping the older server or choosing another `--port`.

## Backup and transfer

Stop the server first. Preserve **both** SQLite databases together with `config/` and any
demo preparation marker/report. Research and qualification have different schemas and must
never share a database file. A case receipt references a research opportunity; backing up just
one database loses part of that relationship.

Use SQLite's `Connection.backup()` for each database rather than copying only a `.sqlite3`
file while a writer may be active. If making a file-level backup after shutdown, preserve any
remaining WAL/SHM sidecars with their database. Keep backups private; do not share `.env`.
For demo-only transfer use its isolated data directory and no credentials. Review any main
workspace source text and contacts before sharing outside the approved client environment.

Code provenance is in `source_manifest.json`. The sibling research project and teammate's
GitHub repository are unchanged. Existing upstream hashes describe the import baseline.

## Known boundaries

No production authentication, multi-tenant isolation, hosted scheduler, automatic outreach,
mail synchronization or Insightly adapter is included. Contact verification and relationship
checks remain human actions. Public signals cannot confirm willingness to buy. Local approval
does not convert mock company configuration into CGS-approved strategy. Confirm production,
data permissions and client ownership separately before a deployment.

# Development roadmap page

`roadmap.py` presents live `design/intention.md` rows alongside explicit progress checkpoints in private `notes/roadmap-status.json`. It stores user comments in private `notes/comments.jsonl`, using AgentBridge's append-only note format. It does not start or access the Bookflow accounting host. No module completion is inferred from a plan file.

`design/bridge.py` is an unmodified upstream AgentBridge utility retrieved from https://raw.githubusercontent.com/5000Stadia/agentbridge/main/tools/bridge.py. SHA256: `0beeae4a01d502e58aff578e89024174957e447c1827d469e57a5aa030414d52`. The wrapper imports its project/parser/note helpers; it does not expose the upstream unauthenticated HTTP server. When using its note CLI directly, always supply `--comments notes/comments.jsonl`.

Launch with Python3.12 and `--root PROJECT --key-file PRIVATE_KEY_FILE --host LOCAL_ADDRESS --port 8787`. The key file contains one independently random 64-character lowercase hex key and must be kept private. The entry bookmark is `http://LOCAL_ADDRESS:8787/access/KEY`. Do not put that key in public docs, a service command line or request logs. The wrapper suppresses request logging and caching. This is a local-network HTTP tool, not an internet/TLS service; do not port-forward it.

Entry creates an independent in-memory session and CSRF secret for at most24hours. Restart invalidates sessions; the same private bookmark can open a new one. Replacing the key invalidates prior sessions on their next request and invalidates the old bookmark. POST requires the configured exact Origin, session and matching CSRF. Only the page and note endpoint are exposed, never arbitrary paths. A stale form may still comment on the module it originally displayed. Removed-row notes remain in archived module cards. Rejected or expired-session note requests retain escaped draft text for copying; they never silently append elsewhere.

The primary development agent updates the dated private progress checkpoint after meaningful module changes and reads waiting comments at normal task boundaries. Notes are input to consider, not automatic commands. For a note's resolution, explain what was folded in or declined, then mark it consumed using the upstream CLI. Preserve completed module identities in the private status file and keep historical comments. Update the status file atomically so readers see a complete checkpoint. User-facing notes on Next/Later can be placed in the general notes area until an owning module is allocated.

Validation uses disposable roadmap projects. It must include legitimate login/comment/readback, invalid login/CSRF, key rotation/session expiry/restart behavior, stale module forms, escaped text and desktop/phone rendering. Never use real company storage or post fake test comments to the user's live notes.

## Completed tags and the notes checkpoint

Completed checkpoint cards default to a visible Completed tag. A completed component may share its module’s row ID while the full module remains active; shared discussions use the live module title. A confirmed defect in original completion criteria changes the entry’s `status` to `Reopened` and explains it in `summary` until a reviewed fix. Additional scope requests are tracked separately. These are explicit dated status updates, never inferred from files disappearing.

Notes to read is the global `consumed=false` queue, oldest timestamp then ID, including completed and archived targets. Page reads never consume notes. Before each new work item, scan this entire queue plus all existing target comments. Record a disposition and consume only reviewed exact IDs. Notes arriving during that review remain for the next checkpoint. Read/consumed means Reviewed by, not necessarily implemented. Historical notes stay in each discussion.

To update an old note, add a follow-up/correction through its discussion or unread card. This creates a new unread entry even if the prior note was consumed or the work completed. There is no in-place history-edit API. Completion status never excludes a note from the checkpoint. This uses AgentBridge’s append-only comment/consume events unchanged.

# Follow-up: orphaned `start_mcp.sh` servers + shared-server question

Source: operator steering, 2026-09-20 (on the AGENTIC-2982 branch, not part of that fix).

## Observed

Multiple stale `apple-mail` MCP server processes accumulate (8 at last count; the
number fluctuates as sessions open/close). Sample: ages from 26 min to 2+ days,
RSS 15–111 MB (~500 MB total), every one parented by a live host (`claude`,
`fx`, `disclaimer`). A reaper scan found **0 true orphans** — all had live parents.

## Proposed upstream fix (operator's words, kept verbatim in intent)

> A legitimate upstream fix, and a different fix than a reaper: because of that
> `exec`, an abnormal host death orphans the server with no last-resort cleanup.
> Either spawn Python as a child with a trap/wait, or have the server exit on its
> own when stdin closes or its parent changes. That fixes it at the source for
> every host and every user, which a local script never can. That's a well-scoped
> issue for the plugin repo.

Concretely: `plugin/start_mcp.sh` ends in `exec python3 apple_mail_mcp.py`,
so the shell is replaced and no trap can run if the host dies abnormally. Fix at
the source: keep Python as a child (trap/wait) or make the server self-exit on
stdin-close / parent-change.

## Separate question: should hosts share one server?

Argument for sharing (operator): one queue instead of eight contended ones —
the server serializes on a single AppleScript lock (parallel Mail calls are
banned for this reason), so eight processes give no parallelism and more timeout
risk; plus one ~68 MB footprint and one permission grant.

Against: single point of failure; version coupling across hosts; needs
supervision (launchd); per-session state (cwd, workspace scoping, session
settings) must move into the protocol or be dropped; a loopback port changes the
security posture vs a stdio child reachable only by its owning host.

## Status

Research + thinking deferred. When picked up: reproduce the orphan mechanism,
decide trap/wait vs self-exit, weigh the sharing tradeoffs above, then file a
Linear issue and implement on its own branch.

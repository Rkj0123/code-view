# Periodic product evals

Run `npm run eval`. These assertions freeze product behavior that is easy to regress while changing the visual layer: complete entry reachability, canonical relationship filters, default test and group visibility, changed-only scope, zero outbound asset URLs, capability handling, launch-response shape, narrow-screen controls, reduced motion, and 5,000-symbol filtering time.

The evals are deterministic and local. The local-server suite owns live HTTP, CSP, capability-header, and generation-refresh integration because those boundaries require the host executable.

The six browser workflows cover same-generation refresh failure/recovery, frozen
command approval and always-available Stop, real Python tic-tac-toe flow, a
generated ten-file flow, out-of-order occurrence evidence, and the mobile workflow.
The flow cases consume real analyzer output, inspect exact relationships, verify
scoped JSON exports, and capture settled light/dark screenshots under
`/tmp/code-view-revamp/critique/`. Source evidence is served from the fixture files,
not invented graph labels. No browser-eval artifact is committed.

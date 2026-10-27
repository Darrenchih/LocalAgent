# Known issues

- REVIEW-044S remains PARTIAL. Numeric comparisons may only enumerate values, and dependent reads may confuse a JSON field with a filename or stop early.
- An 8B first comparison attempt timed out before executing a tool. One bounded retry was partial; no timeout extension.
- 1.7B can format a final answer like a tool-result JSON object. Treat it as model-authored text, not a Broker record or authenticated content/hash binding.
- Native completion stays unverified. Missing-file handling is honest failure reporting, not successful recovery.
- Agent workspace boundary is fixed to the historical isolated test root. Full portability cannot be claimed without a separately authorized security design change.
- Actual APP write, cancellation, invalid-schema and PowerShell acceptance were not performed in S. Historical unit/Native tests are not a substitute.
- Frozen redistribution review is incomplete. Historical notices contain absolute local paths and unresolved dependency/runtime obligations. The exact historical bundle cannot be safely represented as cleared for public distribution.
- No complete Installer acceptance, no model weights, no original Git history, no project-wide MIT license.
- Source rebuilds require Windows toolchains and freshly generated runtime manifests; hashes are integrity pins, not permission to skip review.

Migration records these as GitHub Issues rather than developing new Agent features.

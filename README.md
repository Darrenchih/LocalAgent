# LocalAgent for Windows

Development candidate **0.37.0 — REVIEW-044S PARTIAL**. This is a Windows local Agent project, not a complete production product. Feature development is paused for archival migration.

Uses an external Ollama-compatible local model service. **No model weights, Ollama blobs/manifests, or model cache are included.** Install Ollama and models separately. Existing acceptance used qwen3:1.7b and qwen3:8b; results are observations on those local tags, not guarantees for every model with a similar name.

## Current architecture and evidence

React/TypeScript in a Tauri 2 Windows shell communicates with a Python Core over JSON Lines stdio. The Agent uses a direct tool/result loop with optional plan_task. The Native shell owns individual approval and scope; the Broker performs final checks and file operations. Core/model output cannot create execution authority. Write preview, approved current read hash, dependencies, bounded repair, cancellation and timeouts remain.

REVIEW-044S actual APP tests completed the full Native approval → Broker execution → actual result → model answer chain for basic reads, listing and searching on both models. Original eight-case rubric: 1.7B 6 pass / 1 partial / 1 fail; 8B first attempts 6 pass / 2 fail. A missing-file pass means honest error handling, not successful reading. Comparisons and natural-language dependent reads remain unreliable; a clearer dependent-read supplement succeeded on both models but does not replace original failures. All model stops remain **unverified**, never automatic goal validation.

Historical checks: Python225 passed, Native12 passed/1 ignored, UI66 passed and typecheck passed. These are historical results; see migration verification for current recovery checks. The existing Frozen payload is a --no-bundle development candidate, **not a Windows Installer**.

## Start and rebuild

See [development and recovery](docs/DEVELOPMENT.md), [security model](docs/SECURITY.md), [known issues](docs/KNOWN_ISSUES.md), [release status](docs/RELEASE.md) and [rights](LICENSE).

The source repository deliberately excludes private audit/chat/SQLite records, virtual machines, old delivery evidence, caches, virtual environments and Frozen binaries. First-party source is published under the project owner's migration instruction; no MIT or other blanket open-source license is asserted. Dependencies keep their own licenses and are fetched separately from their registries.

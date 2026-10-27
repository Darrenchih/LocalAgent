# Security and scope

The Native shell controls one approval per actual operation and binds the task to a selected directory. The Broker verifies immutable authority, path constraints and content hashes before execution. Model/tool results are untrusted and do not grant access. No MCP execution, Shared Skills or arbitrary PowerShell is enabled by migration. The PowerShell operation remains the fixed test_json template only.

The historical Native Agent test root is intentionally fixed in production code. It is a security boundary, not a developer-path fallback. This migration does not change it. Therefore general Agent workspace portability outside that root is **not verified/supported**. Chat/Core startup and source recovery can be tested from unrelated directories; that does not prove arbitrary-workspace Agent operation.

Management-only protected runtime remains execution-disabled. Do not confuse that runtime with the separate approved Agent test Broker. No original private security audits, VM identifiers, network topology, conversation databases, approval records or credentials are public assets. Source-level synthetic negative test paths are test data, not real user records.

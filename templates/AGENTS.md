# AGENTS.md — Launchpad Workspace Contract

Welcome to the Launchpad-managed workspace at `{WORKSPACE_ROOT}`.
This document defines boundaries, guarantees, and tools for all autonomous AI coding agents (Claude, Gemini, Cursor, Antigravity, OpenHands, Codex).

---

## 1. Safety Invariants & Agent Boundaries

1. **Never write inside `.launchpad/` directly**:
   - The engine owns `.launchpad/manifest.json`, `config.json`, and `audit.jsonl`.
   - Manifest writes are strictly atomic via `launchpad` CLI tools.
2. **Never delete user work**:
   - Cleanliness audits prevent evicting uncommitted changes or unpushed branches.
   - Non-TTY `--force` calls (agent context) hard-fail by design.
3. **Never auto-push or auto-merge**:
   - Upstream GitHub is canonical; local clones are disposable working trees.
4. **Never create nested workspaces**:
   - All projects live directly at the top level of this directory.

---

## 2. Agent Orientation Commands

```bash
# Check global workspace status & disk health
launchpad status

# Search across all local & remote indexed projects
launchpad search <query>

# Retrieve structured JSON project card (recipe, toolchain, paths)
launchpad context <project-id>

# Promote a remote project and install frozen dependencies
launchpad hydrate <project-id> --hot

# Run a project on an auto-allocated collision-free port
launchpad run <project-id>
```

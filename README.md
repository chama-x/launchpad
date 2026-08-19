# Launchpad

A lightweight workspace manager for macOS that turns Finder and Spotlight into your project launcher and disk lifecycle engine.

Zero resident daemons. Zero background memory. Standard library Python only.

```bash
curl -fsSL https://raw.githubusercontent.com/chama-x/launchpad/main/install.sh | bash
```

---

## The Problem

If you keep dozens of repositories in `~/Projects`, you probably run into the same three headaches:

1. **Storage bloat:** Inactive projects sit untouched for months, holding 50GB+ of throwaway `node_modules`, `.venv`, and `.next` caches.
2. **Context switching:** To open a project's live staging site or GitHub page, you hunt through browser tabs, bookmarks, or terminal history.
3. **Status visibility:** In Finder, every project folder looks identical. You cannot tell which projects have dependencies installed, which are clean, and which have unpushed work.

Most tools try to solve this by running heavy Electron desktop apps, Docker containers, or resident background daemons.

Launchpad takes a different approach: it connects your repositories directly to tools macOS already has—**Spotlight**, **Finder color tags**, and **Quick Look**.

---

<div align="center">
  <img src="assets/finder-native-demo.png" alt="Native macOS Finder Tags" width="100%" style="border-radius: 10px;" />
  <p><em>Finder list view with native readiness tags applied by Launchpad.</em></p>
</div>

---

## Core Capabilities

### 1. Spotlight Launching (`⌘Space`)
Launchpad generates lightweight `.webloc` shortcut descriptors inside a disposable `Launchpad/` index. macOS Spotlight indexes them automatically:

* `⌘Space` $\rightarrow$ `project live` $\rightarrow$ `Return` opens the live deployment in your browser.
* `⌘Space` $\rightarrow$ `project github` $\rightarrow$ `Return` opens the GitHub repository.

<div align="center">
  <img src="assets/spotlight-native-demo.jpg" alt="Native macOS Spotlight Search" width="80%" style="border-radius: 10px;" />
</div>

### 2. Finder Readiness Tags
Launchpad applies native macOS color tags (`libc.setxattr`) to each project folder:

* 🟠 **HOT (Orange):** Materialized repository with active dependencies (`node_modules`, `.venv`). Ready to run.
* 🟡 **WARM (Yellow):** Clean git commit tree with dependencies purged (~1–15 MB on disk).
* ⚪️ **COLD (Gray):** Remote GitHub metadata anchor (0 KB on disk). Not cloned locally until hydrated.
* 🟣 **Pinned (Purple):** Mission-critical or local-only projects permanently protected from automated decay.

### 3. Spacebar Documentation Preview
Every indexed project includes a rendered `README.html`. Tapping **Spacebar** in Finder opens an instant Quick Look preview without launching VS Code or a browser.

### 4. Automated Disk Reclamation
Launchpad automatically scales projects down as they sit idle:

* **After 21 days idle:** Automatically removes `node_modules` and build caches (`HOT` $\rightarrow$ `WARM`).
* **After 120 days idle:** Safely removes the clean local clone (`WARM` $\rightarrow$ `COLD`), keeping the project searchable in Spotlight.
* **Instant Hydration:** Run `launchpad hydrate <project>` (or right-click $\rightarrow$ **Quick Actions** $\rightarrow$ **Hydrate**) to re-clone and run frozen lockfile installs (`pnpm install`, `bun install`, `npm ci`, `cargo build`) in seconds.

---

## Data Safety Guarantees

Launchpad will never delete or evict a project if:

1. `git status` shows untracked or modified files.
2. `git stash list` contains unapplied stashes.
3. `git log` shows unpushed commits on any local branch.

If any check fails, the project is flagged for attention and left untouched.

Non-interactive `--force` calls (such as background scripts or automated AI tools) fail closed with exit code `1`. Force eviction strictly requires an interactive human in a terminal.

---

## AI Agent Interoperability

Launchpad provides structured boundaries for autonomous coding tools (Claude Code, Cursor, Gemini CLI, Antigravity):

* **`AGENTS.md` Contract:** Automatically created at the workspace root (symlinked to `CLAUDE.md` and `GEMINI.md`) to establish workspace rules, safety boundaries, and toolchain paths.
* **Machine-Readable Context:** `launchpad context <project>` outputs structured JSON detailing detected runtime managers (`mise`, `volta`, `fnm`, `nvm`), lockfiles, and run commands.
* **Port Conflict Prevention:** `launchpad run <project>` probes local port occupancy. If port 3000 is occupied, it automatically binds to 3001 (`PORT=3001`), preventing local server collisions during automated agent tasks.

---

## Command Reference

```bash
# Workspace overview, disk headroom, and project counts
launchpad status

# Search across all local and remote indexed projects
launchpad search <query>

# Promote a cold project and install frozen dependencies
launchpad hydrate <project> [--hot]

# Start project on an auto-allocated collision-free port
launchpad run <project>

# Safely evict inactive project and reclaim disk space
launchpad evict <project>

# Protect a project permanently from automated decay
launchpad pin <project>

# Output machine-readable JSON context for AI tools
launchpad context <project>

# Reconcile metadata and upstream renames against GitHub
launchpad sync [--scan-local]

# System diagnostic report with privacy redaction mode
launchpad doctor [--redacted] [--json]
```

---

## Architecture

```
~/Projects/                      ← Workspace root ($LAUNCHPAD_HOME)
├── AGENTS.md                    ← Multi-agent contract (symlinked to CLAUDE.md & GEMINI.md)
├── .launchpad/                  ← Mode 0700 (Engine state — isolated from repos)
│   ├── manifest.json            ← Atomic state store (POSIX os.replace durability)
│   ├── config.json              ← Configurable thresholds and decay settings
│   ├── audit.jsonl              ← Append-only actor audit log
│   └── engine/launchpad.py      ← Pure Python 3 standard library engine
├── Launchpad/                   ← Generated human index (100% disposable)
│   └── <project>/
│       ├── <project> — Live.webloc
│       ├── <project> — GitHub.webloc
│       └── README.html
└── <project>/                   ← Pristine working trees (WARM / HOT)
```

---

## Verification

Launchpad includes a 20-test acceptance suite running in isolated sandboxes with zero side-effects on your real system:

```bash
python3 tests/test_launchpad.py
```

Tested on macOS 13, 14, and 15 (Sonoma & Sequoia) across Python 3.9 through 3.12.

---

## License

Released under the [MIT License](LICENSE). Created by [Chamath Thiwanka](https://github.com/chama-x).

<div align="center">

<img src="assets/hero-keynote.jpg" alt="Launchpad macOS Keynote Banner" width="100%" style="border-radius: 14px; box-shadow: 0 20px 50px rgba(0,0,0,0.6);" />

<br/><br/>

# Launchpad v2

### The macOS-Native Workspace Engine for Developers &amp; AI Coding Agents

**Zero resident daemons. Zero memory footprint. Zero external dependencies. Make the file system itself your launcher, dashboard, and lifecycle manager.**

<br/>

[![macOS 12+](https://img.shields.io/badge/macOS-12%2B%20Sonoma%20%2F%20Sequoia-000000?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/chama-x/launchpad)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B%20Stdlib%20Only-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://github.com/chama-x/launchpad)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-0%20Pip%20Packages-22c55e?style=for-the-badge)](https://github.com/chama-x/launchpad)
[![Tests Passing](https://img.shields.io/badge/Tests-20%2F20%20Passing-38bdf8?style=for-the-badge)](https://github.com/chama-x/launchpad)
[![License: MIT](https://img.shields.io/badge/License-MIT-a855f7?style=for-the-badge)](LICENSE)

<br/>

```bash
# 1-Line Quick Install for macOS
curl -fsSL https://raw.githubusercontent.com/chama-x/launchpad/main/install.sh | bash
```

</div>

---

## ⚡ The Philosophy: The File System IS the UI

Most developer tools try to sell you another 300MB Electron app, a resident Docker daemon eating 4GB RAM, or a custom web dashboard you must keep running forever.

**Launchpad v2 takes the opposite approach:**

macOS Finder, Spotlight, Quick Look, and Automator have a 20-year head start on desktop ergonomics. Instead of fighting the OS, Launchpad turns your native macOS file system into an autonomous, self-maintaining workspace:

* **⌘Space is your Launcher**: Type 3 letters of any project + `live` or `github`, hit **Return**, and your browser opens instantly.
* **Finder is your Dashboard**: Every project folder has a native macOS color tag showing its readiness (`🟠 HOT`, `🟡 WARM`, `⚪️ COLD`, `🟣 Pinned`).
* **Spacebar is your Documentation**: Tap Spacebar on `README.html` for a styled, responsive Quick Look preview without opening an editor.
* **Right-Click is your Control Center**: Right-click any folder $\rightarrow$ **Quick Actions** $\rightarrow$ *Hydrate*, *Run*, or *Evict*.
* **Zero Idle Overhead**: Consumes **0% CPU and 0 MB RAM** while idle. Pure Python 3 standard library with zero background services.

---

<div align="center">
  <img src="assets/hero-spotlight.svg" alt="Launchpad Spotlight Experience" width="100%" style="border-radius: 16px; margin: 20px 0;" />
</div>

---

## 🚀 Key Features

### 1. ⌘Space Spotlight Launching (`< 2-Click Contract`)
Launchpad generates lightweight, native XML `.webloc` plists inside a disposable index (`Launchpad/`). macOS Spotlight indexes them automatically:
* `⌘Space` $\rightarrow$ `openworldeye live` $\rightarrow$ `Return` $\rightarrow$ Opens live deployment in Safari/Chrome.
* `⌘Space` $\rightarrow$ `openworldeye github` $\rightarrow$ `Return` $\rightarrow$ Opens GitHub repository.
* `⌘Space` $\rightarrow$ `README.html` $\rightarrow$ `Spacebar` $\rightarrow$ Instant Quick Look preview.

<div align="center">
  <img src="assets/spotlight-native-demo.jpg" alt="Native macOS Spotlight Search" width="70%" style="border-radius: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.5);" />
</div>

---

### 2. Finder Readiness Tags & Quick Actions
Finder tags are applied natively via `libc.setxattr` on `com.apple.metadata:_kMDItemUserTags`:
* **🟠 `LP · HOT`**: Materialized repository with active dependencies (`node_modules`, `.venv`). Ready to run.
* **🟡 `LP · WARM`**: Lean commit tree with no dependencies. Cloned and ready for development (~1-15 MB).
* **⚪️ `LP · COLD`**: Remote GitHub repository metadata only. Consumes **0 KB on disk** until hydrated.
* **🟣 `LP · Pinned`**: Local-only or mission-critical projects permanently protected from decay.

<div align="center">
  <img src="assets/finder-native-demo.png" alt="Native macOS Finder List View" width="100%" style="border-radius: 14px; box-shadow: 0 20px 50px rgba(0,0,0,0.6);" />
</div>

---

### 3. "Fat When Working, Lean When Resting" (The 3-Tier Lifecycle)

Modern development creates massive dependency bloat—often holding 10GB+ of throwaway `node_modules` and `.next` caches across inactive repos. Launchpad dynamically transitions repositories between three tiers:

<div align="center">
  <img src="assets/lifecycle-architecture.svg" alt="Launchpad 3-Tier Lifecycle" width="100%" style="border-radius: 14px; margin: 20px 0;" />
</div>

* **Auto-Decay Engine**: Unused projects automatically demote from `HOT` $\rightarrow$ `WARM` (after 21 days idle) and `WARM` $\rightarrow$ `COLD` (after 120 days), reclaiming gigabytes of disk space automatically.
* **Instant Hydration**: Run `launchpad hydrate <id> --hot` (or right-click $\rightarrow$ *Hydrate*) to clone and execute frozen-lockfile installs (`pnpm install --frozen-lockfile`, `bun install`, `npm ci`, `cargo build`) in seconds.

---

### 4. Guardrail 3 Cleanliness Audit (Never Destroys Work)

Launchpad enforces strict structural safety before any eviction:
1. **Uncommitted Changes**: Verifies `git status --porcelain` is clean.
2. **Stashes**: Verifies `git stash list` is empty.
3. **Unpushed Commits**: Audits `git log --branches --not --remotes` to guarantee **no unpushed commits exist on any local branch**.
4. **Non-TTY Structural Hard-Block**: If an automated script or AI agent calls `launchpad evict <id> --force` in a non-interactive shell, the engine **hard-fails with exit code 1**. Force-eviction requires an interactive human typing the project ID.

---

### 5. Multi-Agent Workspace Interoperability (`AGENTS.md`)

Launchpad acts as the universal harness for autonomous AI coding agents:
* **`AGENTS.md` (Symlinked to `CLAUDE.md` & `GEMINI.md`)**: Automatically authored at workspace root to instruct Claude Code, Gemini CLI, Cursor, Antigravity, and OpenHands on workspace boundaries.
* **Structured Context Cards**: Agents run `launchpad context <id>` to receive structured JSON project cards with detected toolchains, lockfiles, runtime version managers (`mise` > `volta` > `fnm` > `nvm`), and run scripts.
* **Port Conflict Prober**: `launchpad run <id>` automatically probes port availability. If port `3000` is occupied, it binds to `3001` with `PORT=3001`, preventing server boot collisions.

---

## 📊 Comparison Matrix

| Feature | Launchpad v2 | Docker / DevContainers | PM2 / Resident Daemons | Electron / Custom GUI |
|---|:---:|:---:|:---:|:---:|
| **Idle RAM Overhead** | **0 MB** | 1.5 – 4.0 GB | 150 – 400 MB | 300 – 800 MB |
| **Idle CPU Usage** | **0.0%** | 2 – 8% | 1 – 3% | 0.5 – 2% |
| **External Dependencies** | **Zero (Stdlib)** | Docker Desktop | Node.js + NPM | Build Toolchains |
| **Finder / Spotlight Integration** | **Native** | None | None | Limited |
| **Spacebar Quick Look HTML** | **Yes** | No | No | No |
| **Unpushed Branch Protection** | **Strict Audit** | No | No | No |
| **Multi-Agent Harness Contract** | **Built-in** | Manual | No | No |

---

## 🛠️ CLI Command Reference

```bash
# Check workspace health, disk headroom, and project counts
launchpad status

# Full system diagnostic report with redacted privacy mode
launchpad doctor [--redacted] [--json]

# Search across all local and remote indexed projects
launchpad search <query>

# Retrieve structured project context for AI agents
launchpad context <project-id>

# Promote a remote project and install frozen dependencies
launchpad hydrate <project-id> [--hot]

# Safely evict an inactive project and reclaim disk space
launchpad evict <project-id> [--force]

# Execute a project on an auto-allocated collision-free port
launchpad run <project-id>

# Protect a repository permanently from decay
launchpad pin <project-id>

# Unpin a repository to re-enable automated decay
launchpad unpin <project-id>

# Attach a deployed production/staging URL
launchpad set-url <project-id> https://my-app.vercel.app

# Reconcile metadata & upstream renames against GitHub
launchpad sync [--scan-local]

# Regenerate all Launchpad/ slots, .webloc files, and Finder tags
launchpad regenerate
```

---

## 🏗️ Architecture & Directory Layout

```
~/Projects/ (or $LAUNCHPAD_HOME)
├── AGENTS.md                    ← Multi-agent harness contract (CLAUDE.md & GEMINI.md symlinked)
├── .launchpad/                  ← Mode 0700 (Engine-owned state)
│   ├── manifest.json            ← Single source of truth (Atomic temp writes + os.replace)
│   ├── config.json              ← Global thresholds & decay settings
│   ├── audit.jsonl              ← Append-only actor audit trail
│   └── engine/launchpad.py      ← Single-file engine (Pure Python 3 stdlib)
├── Launchpad/                   ← GENERATED human index (100% disposable & regenerable)
│   └── <project>/
│       ├── <project> — Live.webloc     ← Double-click → Browser (Spotlight indexed)
│       ├── <project> — GitHub.webloc   ← Double-click → GitHub repo
│       ├── README.md                   ← Spacebar → Quick Look markdown view
│       └── README.html                 ← Spacebar → Rendered styled HTML Quick Look
└── <project>/                   ← Materialized working trees (WARM / HOT) — 100% pristine
```

---

## 🧪 Testing & CI

Launchpad includes an automated 20-test acceptance suite running in isolated sandboxes with zero side-effects on your real system:

```bash
# Run unit & integration tests
python3 tests/test_launchpad.py
```

Tested across **macOS 13, 14, and 15 (Sonoma & Sequoia)** on Python 3.9, 3.10, 3.11, and 3.12.

---

## 📄 License & Author

Created and maintained by **[Chamath Thiwanka](https://github.com/chama-x)**.

Released under the **[MIT License](LICENSE)**.

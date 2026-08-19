# Changelog

All notable changes to **Launchpad** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-08-19

### Added
- **macOS-Native UI Architecture**: Replaced custom Electron/Tauri desktop apps with native Finder tags, Spotlight `.webloc` indexing, and Quick Look previews.
- **Pure Python 3 Stdlib Engine**: Single-file engine with zero external runtime dependencies (`plistlib`, `ctypes`, `subprocess`, `json`, `pathlib`).
- **Rendered Quick Look Previews**: Automatically generated `README.html` with dark/light mode CSS for styled Spacebar previews in Finder.
- **Stable GitHub ID Reconciliation**: Sync queries numeric/node GitHub IDs, making upstream repository renames and transfers 100% deterministic.
- **Guardrail 3 Cleanliness Audit**: Strict pre-eviction checks for uncommitted changes, stashes, and unpushed branches across all refs.
- **Non-TTY Protection**: Hard-failure on non-interactive `--force` evictions to prevent autonomous AI agents from destroying work.
- **Automatic Port Collision Probing**: Automatically detects occupied development ports (e.g. 3000) and allocates the next available port (`PORT=3001`).
- **Finder Quick Actions**: 6 Automator workflow bundles installed in `~/Library/Services/` with native notifications.
- **Folder Action Auto-Watcher**: Debounced AppleScript watcher for automatic in-place adoption of new folders.
- **Multi-Agent Workspace Contract**: Standardized `AGENTS.md` spec with symlinks for Claude (`CLAUDE.md`) and Gemini (`GEMINI.md`).
- **Comprehensive Test Suite**: 20 unit and integration tests verifying all 4 architectural phases.

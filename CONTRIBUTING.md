# Contributing to Launchpad

Thank you for your interest in contributing to **Launchpad**!

Launchpad is built on a strict architectural principle: **Make the file system itself the UI**.
We strictly reject resident background daemons, heavy GUI frameworks (Electron/Tauri), and external runtime dependencies.

---

## Core Invariants to Preserve

1. **Zero External Dependencies**: Pure Python 3 standard library only (`subprocess`, `plistlib`, `ctypes`, `json`, `pathlib`, `shutil`, `http.server`).
2. **Never Write Inside Project Repositories**: The engine only writes to `.launchpad/` and `Launchpad/` (the only exception is `git remote set-url` during upstream rename sync).
3. **Never Move or Delete User Folders**: Adoption is strictly in-place and non-destructive.
4. **Data Safety Gate**: Eviction strictly blocks if uncommitted changes, stashes, or unpushed branches exist. Non-TTY `--force` calls must hard-fail.
5. **Atomic Manifest Writes**: All updates to `manifest.json` must use temporary files + atomic `os.replace`.

---

## Development & Testing

1. Clone the repository:
   ```bash
   git clone https://github.com/chama-x/launchpad.git
   cd launchpad
   ```

2. Run the acceptance test suite:
   ```bash
   python3 tests/test_launchpad.py
   ```

3. Validate changes in isolation:
   All unit and integration tests run inside isolated temporary sandboxes with zero side-effects on your real system.

---

## Submitting Pull Requests

1. Fork the repo and create your branch from `main`.
2. Ensure all tests pass (`python3 tests/test_launchpad.py`).
3. Maintain documentation and code comments.
4. Open a PR with a clear description of the problem solved.

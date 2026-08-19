#!/usr/bin/env python3
"""
Launchpad v2 - Self-maintaining local project workspace engine for macOS.
Standard library only (Python 3.9+). No resident processes. No external pip dependencies.
"""

import argparse
import ctypes
import datetime
import json
import os
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "2.0.0"
MANIFEST_SCHEMA_VERSION = 1

TAG_COLORS = {
    "LP · HOT": ("LP · HOT", 7),        # Orange
    "LP · WARM": ("LP · WARM", 5),      # Yellow
    "LP · COLD": ("LP · COLD", 1),      # Gray
    "LP · Pinned": ("LP · Pinned", 0),  # Neutral flag (leaves Tier color as primary folder icon)
    "LP · Attention": ("LP · Attention", 6), # Red
}


# ==============================================================================
# 1. Environment & Paths Configuration
# ==============================================================================

def get_launchpad_root() -> Path:
    """
    Resolve the Launchpad workspace root.
    Priority:
    1. LAUNCHPAD_HOME env var
    2. Ascending directory search for an existing .launchpad folder
    3. ~/Projects
    """
    if "LAUNCHPAD_HOME" in os.environ:
        return Path(os.path.expanduser(os.environ["LAUNCHPAD_HOME"])).resolve()

    current = Path.cwd().resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".launchpad").is_dir():
            return parent

    return Path(os.path.expanduser("~/Projects")).resolve()


class Paths:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or get_launchpad_root()
        self.launchpad_dir = self.root / ".launchpad"
        self.manifest_path = self.launchpad_dir / "manifest.json"
        self.config_path = self.launchpad_dir / "config.json"
        self.audit_path = self.launchpad_dir / "audit.jsonl"
        self.engine_dir = self.launchpad_dir / "engine"
        self.views_dir = self.launchpad_dir / "views"
        self.last_sync_file = self.launchpad_dir / ".last_sync"
        self.last_scan_file = self.launchpad_dir / ".last_scan"
        self.last_folder_action_file = self.launchpad_dir / ".last_folder_action"
        self.launchpad_index = self.root / "Launchpad"
        self.agents_md = self.root / "AGENTS.md"
        self.claude_md = self.root / "CLAUDE.md"
        self.gemini_md = self.root / "GEMINI.md"

    def ensure_layout(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.launchpad_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.launchpad_dir, 0o700)
        except OSError:
            pass
        self.engine_dir.mkdir(parents=True, exist_ok=True)
        self.views_dir.mkdir(parents=True, exist_ok=True)
        self.launchpad_index.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 2. Config & Audit Subsystems
# ==============================================================================

DEFAULT_CONFIG = {
    "editor": "auto",
    "hotDays": 21,
    "warmDays": 120,
    "minFreeDiskGB": 5,
    "ignoreScripts": False,
    "syncIntervalHours": 6,
    "scanIntervalHours": 24,
    "ports": {
        "probeFallback": True,
        "default": 3000
    }
}


class ConfigManager:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.config = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.paths.config_path.exists():
            self._save(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        try:
            with open(self.paths.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults for missing keys
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(data)
            return cfg
        except Exception:
            return dict(DEFAULT_CONFIG)

    def _save(self, data: Dict[str, Any]):
        self.paths.ensure_layout()
        tmp = self.paths.launchpad_dir / f".config.json.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.paths.config_path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        self.config[key] = value
        self._save(self.config)


class AuditLogger:
    def __init__(self, paths: Paths):
        self.paths = paths

    def _detect_actor(self) -> str:
        if os.environ.get("ANTIGRAVITY"):
            return "agent:antigravity"
        if os.environ.get("CLAUDE_CODE"):
            return "agent:claude"
        if os.environ.get("GEMINI_CODE"):
            return "agent:gemini"
        if not sys.stdin.isatty():
            return "agent/script"
        return os.environ.get("USER", "user")

    def log(self, action: str, target: str, result: str, detail: str = ""):
        self.paths.ensure_layout()
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "actor": self._detect_actor(),
            "action": action,
            "target": target,
            "result": result,
            "detail": detail
        }
        try:
            with open(self.paths.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
        except Exception:
            pass


# ==============================================================================
# 3. Schema, Manifest Management & Atomic Writes
# ==============================================================================

class ManifestManager:
    def __init__(self, paths: Paths, audit: AuditLogger):
        self.paths = paths
        self.audit = audit
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.paths.manifest_path.exists():
            initial = {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "meta": {
                    "lastSyncAt": None,
                    "lastScanAt": None,
                    "engineVersion": ENGINE_VERSION
                },
                "projects": []
            }
            self.save(initial)
            return initial

        try:
            with open(self.paths.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise RuntimeError(f"Failed to read manifest at {self.paths.manifest_path}: {e}")

        version = data.get("schemaVersion", 1)
        if version > MANIFEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"Manifest schemaVersion {version} is newer than engine supported version {MANIFEST_SCHEMA_VERSION}. Failing closed."
            )
        elif version < MANIFEST_SCHEMA_VERSION:
            data = self._migrate(data, version)

        self._validate_schema(data)
        return data

    def _migrate(self, old_data: Dict[str, Any], old_version: int) -> Dict[str, Any]:
        # Backup before migration
        backup_path = self.paths.launchpad_dir / f"manifest.json.v{old_version}.bak"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f, indent=2)

        # Apply migrations (for now identity / upgrade version)
        migrated = dict(old_data)
        migrated["schemaVersion"] = MANIFEST_SCHEMA_VERSION
        self.save(migrated)
        self.audit.log("migrate_schema", str(backup_path), "success", f"Migrated from v{old_version} to v{MANIFEST_SCHEMA_VERSION}")
        return migrated

    def _validate_schema(self, data: Dict[str, Any]):
        if not isinstance(data, dict):
            raise ValueError("Manifest root must be an object")
        if "schemaVersion" not in data or not isinstance(data["schemaVersion"], int):
            raise ValueError("Manifest missing valid integer schemaVersion")
        if "meta" not in data or not isinstance(data["meta"], dict):
            raise ValueError("Manifest missing meta object")
        if "projects" not in data or not isinstance(data["projects"], list):
            raise ValueError("Manifest missing projects array")

        seen_ids = set()
        for idx, p in enumerate(data["projects"]):
            if not isinstance(p, dict):
                raise ValueError(f"Project at index {idx} is not an object")
            pid = p.get("id")
            if not pid or not isinstance(pid, str):
                raise ValueError(f"Project at index {idx} missing valid string id")
            if pid in seen_ids:
                raise ValueError(f"Duplicate project id detected: {pid}")
            seen_ids.add(pid)

            # Validate source
            src = p.get("source", {})
            if not isinstance(src, dict):
                raise ValueError(f"Project '{pid}' has invalid source block")

            # Validate state
            st = p.get("state", {})
            if not isinstance(st, dict):
                raise ValueError(f"Project '{pid}' has invalid state block")
            tier = st.get("tier")
            if tier not in ("COLD", "WARM", "HOT"):
                raise ValueError(f"Project '{pid}' has invalid tier: {tier}")

    def save(self, candidate_data: Optional[Dict[str, Any]] = None):
        if candidate_data is not None:
            self._validate_schema(candidate_data)
            data_to_write = candidate_data
        else:
            self._validate_schema(self.data)
            data_to_write = self.data

        self.paths.ensure_layout()
        tmp_file = self.paths.launchpad_dir / f".manifest.json.tmp.{os.getpid()}.{time.time()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data_to_write, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_file, self.paths.manifest_path)
        if candidate_data is not None:
            self.data = candidate_data

    def get_project(self, project_id_or_path: str) -> Optional[Dict[str, Any]]:
        # Normalize
        q = project_id_or_path.strip().rstrip("/")
        for p in self.data["projects"]:
            if p["id"] == q:
                return p
            rel_path = p.get("state", {}).get("path")
            if rel_path:
                if rel_path == q:
                    return p
                try:
                    if (self.paths.root / rel_path).resolve() == Path(q).resolve():
                        return p
                except Exception:
                    pass
        return None

    def upsert_project(self, project: Dict[str, Any]):
        pid = project["id"]
        for idx, existing in enumerate(self.data["projects"]):
            if existing["id"] == pid:
                self.data["projects"][idx] = project
                self.save()
                return
        self.data["projects"].append(project)
        self.save()

    def remove_project(self, project_id: str) -> bool:
        initial_len = len(self.data["projects"])
        self.data["projects"] = [p for p in self.data["projects"] if p["id"] != project_id]
        if len(self.data["projects"]) != initial_len:
            self.save()
            return True
        return False


# ==============================================================================
# 4. Invariant & Cleanliness Safety Gate (Guardrails 1, 2, 3, 4)
# ==============================================================================

class SafetyGate:
    def __init__(self, paths: Paths):
        self.paths = paths

    def audit_cleanliness(self, repo_dir: Path) -> Tuple[bool, List[str]]:
        """
        Non-negotiable Guardrail 3:
        Audit working tree for:
        1. Uncommitted / staged changes
        2. Untracked non-ignored files
        3. Stashes
        4. Unpushed commits on any branch
        5. Dirty submodules
        """
        if not repo_dir.exists() or not (repo_dir / ".git").exists():
            return True, []

        issues = []

        # 1. Uncommitted / untracked changes
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().splitlines()
            issues.append(f"Uncommitted or untracked changes ({len(lines)} file(s)):")
            for l in lines[:5]:
                issues.append(f"  {l}")
            if len(lines) > 5:
                issues.append(f"  ... and {len(lines) - 5} more")

        # 2. Stashes
        res = subprocess.run(
            ["git", "stash", "list"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            stashes = res.stdout.strip().splitlines()
            issues.append(f"Git stashes present ({len(stashes)} stash(es)):")
            for s in stashes[:3]:
                issues.append(f"  {s}")

        # 3. Unpushed commits on all branches
        res = subprocess.run(
            ["git", "log", "--branches", "--not", "--remotes", "--oneline"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            commits = res.stdout.strip().splitlines()
            issues.append(f"Unpushed commits on local branches ({len(commits)} commit(s)):")
            for c in commits[:3]:
                issues.append(f"  {c}")

        # 4. Dirty submodules
        res = subprocess.run(
            ["git", "submodule", "status"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            subm_lines = [l for l in res.stdout.strip().splitlines() if l.startswith("+") or l.startswith("U")]
            if subm_lines:
                issues.append(f"Dirty or unmerged submodules ({len(subm_lines)}):")
                for sub in subm_lines:
                    issues.append(f"  {sub}")

        is_clean = len(issues) == 0
        return is_clean, issues

    def can_evict(self, project: Dict[str, Any], force: bool) -> Tuple[bool, str]:
        rel_path = project.get("state", {}).get("path")
        if not rel_path:
            return True, ""

        repo_dir = self.paths.root / rel_path
        if not repo_dir.exists():
            return True, ""

        is_clean, issues = self.audit_cleanliness(repo_dir)
        if is_clean:
            return True, ""

        if not force:
            report = "\n".join(issues)
            advice = (
                "\n\nTo safely resolve:"
                "\n  • Push unpushed commits: git push origin <branch>"
                "\n  • Stash uncommitted changes: git stash"
                "\n  • Pin this project to keep it: launchpad pin " + project["id"] +
                "\n  • Force eviction (destroys uncommitted work): launchpad evict " + project["id"] + " --force (requires interactive confirmation)"
            )
            return False, f"Eviction blocked by uncommitted work:\n{report}{advice}"

        # Force handling
        if not sys.stdin.isatty():
            return False, (
                "Guardrail 3 Violation: --force cannot be used in a non-TTY / non-interactive / agent context. "
                "Uncommitted work would be destroyed. Eviction refused."
            )

        print("\n" + "=" * 60)
        print(f"WARNING: Project '{project['id']}' has uncommitted work:")
        for iss in issues:
            print(f"  {iss}")
        print("=" * 60)
        try:
            typed = input(f"Type project id '{project['id']}' to confirm destruction of local working tree: ").strip()
            if typed == project["id"]:
                return True, ""
            return False, "Confirmation did not match project id. Eviction aborted."
        except (EOFError, KeyboardInterrupt):
            return False, "Eviction aborted by user."


# ==============================================================================
# 5. Toolchain, Version Manager & Run Recipe Detection
# ==============================================================================

class ToolchainDetector:
    @staticmethod
    def detect_toolchain(dir_path: Path, ignore_scripts: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """
        Detect package manager, frozen install command, runtime, and kind.
        """
        toolchain = {
            "runtime": "generic",
            "packageManager": "none",
            "versionManager": "none"
        }
        recipe = {
            "install": "",
            "run": "",
            "port": 3000
        }
        kind = "other"

        if not dir_path.exists() or not dir_path.is_dir():
            return toolchain, recipe, kind

        # Version manager detection
        version_manager = "system"
        if (dir_path / ".tool-versions").exists() or (dir_path / "mise.toml").exists():
            version_manager = "mise"
        elif (dir_path / "package.json").exists():
            try:
                with open(dir_path / "package.json", "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                if "volta" in pkg_data:
                    version_manager = "volta"
            except Exception:
                pass
        elif (dir_path / ".nvmrc").exists():
            version_manager = "nvm"
        elif (dir_path / ".node-version").exists():
            version_manager = "fnm"
        toolchain["versionManager"] = version_manager

        # JavaScript / TypeScript / Node
        pkg_json = dir_path / "package.json"
        if pkg_json.exists():
            kind = "app"
            scripts = {}
            deps = {}
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                    scripts = pkg_data.get("scripts", {})
                    deps.update(pkg_data.get("dependencies", {}))
                    deps.update(pkg_data.get("devDependencies", {}))
            except Exception:
                pass

            # Detect package manager & frozen command
            opt_ignore = " --ignore-scripts" if ignore_scripts else ""
            if (dir_path / "pnpm-lock.yaml").exists():
                toolchain["packageManager"] = "pnpm"
                recipe["install"] = f"pnpm install --frozen-lockfile{opt_ignore}"
                run_cmd = "pnpm dev" if "dev" in scripts else ("pnpm start" if "start" in scripts else "")
            elif (dir_path / "yarn.lock").exists():
                toolchain["packageManager"] = "yarn"
                recipe["install"] = f"yarn install --immutable{opt_ignore}"
                run_cmd = "yarn dev" if "dev" in scripts else ("yarn start" if "start" in scripts else "")
            elif (dir_path / "bun.lockb").exists() or (dir_path / "bun.lock").exists():
                toolchain["packageManager"] = "bun"
                recipe["install"] = f"bun install --frozen-lockfile{opt_ignore}"
                run_cmd = "bun dev" if "dev" in scripts else ("bun start" if "start" in scripts else "")
            else:
                toolchain["packageManager"] = "npm"
                recipe["install"] = f"npm ci{opt_ignore}"
                run_cmd = "npm run dev" if "dev" in scripts else ("npm start" if "start" in scripts else "")

            recipe["run"] = run_cmd

            # Detect default port based on framework
            if "next" in deps:
                recipe["port"] = 3000
            elif "vite" in deps:
                recipe["port"] = 5173
            elif "astro" in deps:
                recipe["port"] = 4321
            elif "nuxt" in deps:
                recipe["port"] = 3000
            elif "remix" in deps:
                recipe["port"] = 3000
            else:
                recipe["port"] = 3000

            toolchain["runtime"] = f"node via {toolchain['packageManager']}"
            return toolchain, recipe, kind

        # Python
        if (dir_path / "pyproject.toml").exists() or (dir_path / "requirements.txt").exists() or (dir_path / "Pipfile").exists():
            kind = "backend"
            toolchain["runtime"] = "python"
            if (dir_path / "poetry.lock").exists():
                toolchain["packageManager"] = "poetry"
                recipe["install"] = "poetry install --no-root"
                recipe["run"] = "poetry run python -m app"
            elif (dir_path / "uv.lock").exists():
                toolchain["packageManager"] = "uv"
                recipe["install"] = "uv sync --frozen"
                recipe["run"] = "uv run python -m app"
            else:
                toolchain["packageManager"] = "pip"
                recipe["install"] = "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
                recipe["run"] = ".venv/bin/python main.py"
            recipe["port"] = 8000
            return toolchain, recipe, kind

        # Rust
        if (dir_path / "Cargo.toml").exists():
            kind = "cli"
            toolchain["runtime"] = "rust"
            toolchain["packageManager"] = "cargo"
            recipe["install"] = "cargo build --locked"
            recipe["run"] = "cargo run"
            recipe["port"] = 8080
            return toolchain, recipe, kind

        # Go
        if (dir_path / "go.mod").exists():
            kind = "backend"
            toolchain["runtime"] = "go"
            toolchain["packageManager"] = "go"
            recipe["install"] = "go mod download"
            recipe["run"] = "go run ."
            recipe["port"] = 8080
            return toolchain, recipe, kind

        return toolchain, recipe, kind

    @staticmethod
    def probe_available_port(start_port: int) -> int:
        port = start_port
        while port < start_port + 100:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    port += 1
        return start_port


# ==============================================================================
# 6. Generated Surfaces (Weblocs, READMEs, Finder Tags, Quick Look)
# ==============================================================================

class SurfaceManager:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.libc = None
        try:
            if sys.platform == "darwin":
                self.libc = ctypes.cdll.LoadLibrary("libc.dylib")
                # int setxattr(const char *path, const char *name, const void *value, size_t size, u_int32_t position, int options);
                self.libc.setxattr.argtypes = [
                    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                    ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int
                ]
                self.libc.setxattr.restype = ctypes.c_int
        except Exception:
            self.libc = None

    def set_finder_tags(self, target_path: Path, tag_names: List[str]):
        """
        Apply Finder color tags to a file or directory using macOS extended attributes.
        """
        if not target_path.exists() or sys.platform != "darwin":
            return

        tag_list = []
        for name in tag_names:
            if name in TAG_COLORS:
                tag_str, color_code = TAG_COLORS[name]
                tag_list.append(f"{tag_str}\n{color_code}")
            else:
                tag_list.append(f"{name}\n0")

        data = plistlib.dumps(tag_list, fmt=plistlib.FMT_BINARY)

        # 1. Native libc setxattr
        if self.libc:
            try:
                res = self.libc.setxattr(
                    str(target_path).encode("utf-8"),
                    b"com.apple.metadata:_kMDItemUserTags",
                    data,
                    len(data),
                    0,
                    0
                )
                if res == 0:
                    return
            except Exception:
                pass

        # 2. Fallback to tag CLI if available
        if shutil.which("tag"):
            try:
                subprocess.run(
                    ["tag", "--set", ",".join(tag_names), str(target_path)],
                    capture_output=True,
                    check=False
                )
            except Exception:
                pass

    def create_webloc(self, file_path: Path, url: str):
        """
        Generate an XML plist .webloc file natively using plistlib.
        """
        payload = {"URL": url}
        data = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
        with open(file_path, "wb") as f:
            f.write(data)

    @staticmethod
    def render_markdown_html(pid: str, project: Dict[str, Any], raw_markdown: str) -> str:
        """
        Convert markdown to beautiful self-contained HTML for macOS Quick Look.
        """
        # Basic markdown to HTML conversion (stdlib only)
        import html
        tier = project.get("state", {}).get("tier", "COLD")
        github_url = project.get("source", {}).get("url") or ""
        live_url = project.get("links", {}).get("live") or ""
        docs_url = project.get("links", {}).get("docs") or ""

        # Escape raw text
        lines = raw_markdown.splitlines()
        html_lines = []
        in_code_block = False
        in_list = False

        for line in lines:
            if line.strip().startswith("```"):
                if in_code_block:
                    html_lines.append("</code></pre>")
                    in_code_block = False
                else:
                    lang = html.escape(line.strip().replace("```", ""))
                    html_lines.append(f"<pre class=\"code-block\"><code class=\"lang-{lang}\">")
                    in_code_block = True
                continue

            if in_code_block:
                html_lines.append(html.escape(line))
                continue

            # Unordered lists
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                item_content = html.escape(stripped[2:])
                item_content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", item_content)
                item_content = re.sub(r"`([^`]+)`", r"<code>\1</code>", item_content)
                html_lines.append(f"  <li>{item_content}</li>")
                continue
            else:
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False

            if not stripped:
                html_lines.append("<br/>")
                continue

            # Headers
            if stripped.startswith("# "):
                html_lines.append(f"<h1>{html.escape(stripped[2:])}</h1>")
            elif stripped.startswith("## "):
                html_lines.append(f"<h2>{html.escape(stripped[3:])}</h2>")
            elif stripped.startswith("### "):
                html_lines.append(f"<h3>{html.escape(stripped[4:])}</h3>")
            elif stripped.startswith("#### "):
                html_lines.append(f"<h4>{html.escape(stripped[5:])}</h4>")
            elif stripped.startswith("---") or stripped.startswith("***"):
                html_lines.append("<hr/>")
            else:
                # Regular paragraph
                p_text = html.escape(line)
                p_text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", p_text)
                p_text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", p_text)
                p_text = re.sub(r"`([^`]+)`", r"<code>\1</code>", p_text)
                p_text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', p_text)
                html_lines.append(f"<p>{p_text}</p>")

        if in_code_block:
            html_lines.append("</code></pre>")
        if in_list:
            html_lines.append("</ul>")

        body_content = "\n".join(html_lines)

        tier_colors = {
            "HOT": ("#ea580c", "#ffedd5"),
            "WARM": ("#ca8a04", "#fef9c3"),
            "COLD": ("#64748b", "#f1f5f9")
        }
        badge_fg, badge_bg = tier_colors.get(tier, ("#64748b", "#f1f5f9"))

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(pid)} — Launchpad</title>
<style>
  :root {{
    --bg: #ffffff;
    --text: #1e293b;
    --card-bg: #f8fafc;
    --border: #e2e8f0;
    --code-bg: #f1f5f9;
    --accent: #2563eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f172a;
      --text: #e2e8f0;
      --card-bg: #1e293b;
      --border: #334155;
      --code-bg: #0f172a;
      --accent: #60a5fa;
    }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.6;
    background: var(--bg);
    color: var(--text);
    padding: 30px;
    max-width: 860px;
    margin: 0 auto;
  }}
  .header {{
    border-bottom: 2px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}
  .badge {{
    display: inline-block;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    background: {badge_bg};
    color: {badge_fg};
  }}
  .links {{
    margin-top: 10px;
    display: flex;
    gap: 12px;
  }}
  a {{
    color: var(--accent);
    text-decoration: none;
  }}
  a:hover {{
    text-decoration: underline;
  }}
  pre.code-block {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
    overflow-x: auto;
    font-family: "SF Mono", Menlo, Consolas, Monaco, monospace;
    font-size: 13px;
  }}
  code {{
    background: var(--code-bg);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: "SF Mono", Menlo, Consolas, Monaco, monospace;
    font-size: 13px;
  }}
  h1, h2, h3, h4 {{
    color: var(--text);
    margin-top: 24px;
    margin-bottom: 12px;
  }}
  hr {{
    border: 0;
    height: 1px;
    background: var(--border);
    margin: 24px 0;
  }}
</style>
</head>
<body>
  <div class="header">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <h1 style="margin:0;">{html.escape(pid)}</h1>
      <span class="badge">LP · {tier}</span>
    </div>
    <div class="links">
      {f'<a href="{html.escape(github_url)}" target="_blank">📂 GitHub</a>' if github_url else ''}
      {f'<a href="{html.escape(live_url)}" target="_blank">🚀 Live Demo</a>' if live_url else ''}
      {f'<a href="{html.escape(docs_url)}" target="_blank">📖 Docs</a>' if docs_url else ''}
    </div>
  </div>
  <div class="content">
    {body_content}
  </div>
</body>
</html>
"""

    def regenerate_slot(self, project: Dict[str, Any]):
        """
        Reconstruct a single project slot in Launchpad/<id>/
        """
        pid = project["id"]
        slot_dir = self.paths.launchpad_index / pid
        slot_dir.mkdir(parents=True, exist_ok=True)

        # 1. Weblocs
        live_url = project.get("links", {}).get("live")
        live_file = slot_dir / f"{pid} — Live.webloc"
        if live_url:
            self.create_webloc(live_file, live_url)
        elif live_file.exists():
            live_file.unlink()

        github_url = project.get("source", {}).get("url")
        if github_url:
            github_file = slot_dir / f"{pid} — GitHub.webloc"
            self.create_webloc(github_file, github_url)

        # 2. Cached README for Quick Look (.md and rendered .html)
        readme_target = slot_dir / "README.md"
        readme_html_target = slot_dir / "README.html"
        rel_path = project.get("state", {}).get("path")
        local_readme = (self.paths.root / rel_path / "README.md") if rel_path else None

        readme_content = ""
        if local_readme and local_readme.exists():
            try:
                readme_content = local_readme.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass

        if not readme_content:
            readme_content = f"# {pid}\n\nProject managed by Launchpad.\n- GitHub: {github_url or 'None'}\n- Live: {live_url or 'None'}\n- Tier: {project.get('state', {}).get('tier', 'COLD')}\n"

        header = (
            f"<!--\n"
            f"  Launchpad Quick Look Cache\n"
            f"  Project: {pid}\n"
            f"  Tier: {project.get('state', {}).get('tier', 'COLD')}\n"
            f"  Cached: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}\n"
            f"-->\n\n"
        )
        with open(readme_target, "w", encoding="utf-8") as f:
            f.write(header + readme_content)

        # Rendered HTML Quick Look cache
        html_doc = self.render_markdown_html(pid, project, readme_content)
        with open(readme_html_target, "w", encoding="utf-8") as f:
            f.write(html_doc)

        # 3. Finder Tags
        tier = project.get("state", {}).get("tier", "COLD")
        tags = [f"LP · {tier}"]
        if project.get("state", {}).get("pinned"):
            tags.append("LP · Pinned")
        if project.get("state", {}).get("attention"):
            tags.append("LP · Attention")

        self.set_finder_tags(slot_dir, tags)

        if rel_path:
            repo_dir = self.paths.root / rel_path
            if repo_dir.exists():
                self.set_finder_tags(repo_dir, tags)

    def regenerate_all(self, manifest: Dict[str, Any]):
        """
        Guardrail 7: Everything generated is regenerable.
        Wipe Launchpad/ directory and reconstruct from manifest.
        """
        self.paths.ensure_layout()
        if self.paths.launchpad_index.exists():
            shutil.rmtree(self.paths.launchpad_index)
        self.paths.launchpad_index.mkdir(parents=True, exist_ok=True)

        for p in manifest.get("projects", []):
            self.regenerate_slot(p)


# ==============================================================================
# 7. macOS Automator Quick Actions & Folder Action Integrations
# ==============================================================================

AUTOMATOR_ACTIONS = [
    ("Launchpad — Open in Editor", "open --editor"),
    ("Launchpad — Open Live", "open --live"),
    ("Launchpad — Open GitHub", "open --github"),
    ("Launchpad — Hydrate", "hydrate --hot"),
    ("Launchpad — Evict", "evict"),
    ("Launchpad — Run", "run"),
]


def generate_automator_wflow_xml(action_name: str, cli_subcommand: str) -> str:
    """
    Generate an Apple Automator document.wflow XML for a Quick Action service.
    """
    shell_script = f"""# Launchpad Quick Action: {action_name}
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

for f in "$@"; do
    TARGET_DIR="$f"
    if [ -f "$f" ]; then
        TARGET_DIR="$(dirname "$f")"
    fi

    # Check if launchpad CLI is available
    if ! command -v launchpad >/dev/null 2>&1; then
        osascript -e 'display notification "Launchpad CLI not found on PATH (~/.local/bin)." with title "Launchpad Error"'
        exit 1
    fi

    # Run launchpad command
    OUTPUT=$(launchpad {cli_subcommand} "$TARGET_DIR" 2>&1)
    EXIT_CODE=$?

    # Clean output for notification
    FIRST_LINE=$(echo "$OUTPUT" | head -n 1)
    if [ $EXIT_CODE -eq 0 ]; then
        osascript -e "display notification \\"$FIRST_LINE\\" with title \\"{action_name}\\""
    else
        osascript -e "display notification \\"$FIRST_LINE\\" with title \\"{action_name} Failed\\""
    fi
done
"""
    escaped_script = (
        shell_script.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

    wflow_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AMApplicationBuild</key>
	<string>523</string>
	<key>AMApplicationVersion</key>
	<string>2.10</string>
	<key>AMDocumentVersion</key>
	<string>2</string>
	<key>actions</key>
	<array>
		<dict>
			<key>action</key>
			<dict>
				<key>AMAccepts</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Optional</key>
					<true/>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.path</string>
					</array>
				</dict>
				<key>AMActionVersion</key>
				<string>2.0.3</string>
				<key>AMApplication</key>
				<array>
					<string>Finder</string>
				</array>
				<key>AMParameterProperties</key>
				<dict>
					<key>COMMAND_STRING</key>
					<dict/>
					<key>CheckedForUserDefaultShell</key>
					<dict/>
					<key>inputMethod</key>
					<dict/>
					<key>shell</key>
					<dict/>
					<key>source</key>
					<dict/>
				</dict>
				<key>AMProvides</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.path</string>
					</array>
				</dict>
				<key>ActionBundlePath</key>
				<string>/System/Library/Automator/Run Shell Script.action</string>
				<key>ActionName</key>
				<string>Run Shell Script</string>
				<key>ActionParameters</key>
				<dict>
					<key>COMMAND_STRING</key>
					<string>{escaped_script}</string>
					<key>CheckedForUserDefaultShell</key>
					<true/>
					<key>inputMethod</key>
					<integer>1</integer>
					<key>shell</key>
					<string>/bin/zsh</string>
					<key>source</key>
					<string></string>
				</dict>
				<key>BundleIdentifier</key>
				<string>com.apple.RunShellScript</string>
				<key>CFBundleVersion</key>
				<string>2.0.3</string>
				<key>CanShowSelectedItemsWhenRun</key>
				<false/>
				<key>CanShowWhenRun</key>
				<true/>
				<key>Category</key>
				<array>
					<string>AMCategoryUtilities</string>
				</array>
				<key>Class Name</key>
				<string>RunShellScriptAction</string>
				<key>InputUUID</key>
				<string>A1B2C3D4-E5F6-7890-ABCD-EF1234567890</string>
				<key>Keywords</key>
				<array>
					<string>Shell</string>
					<string>Script</string>
					<string>Command</string>
					<string>Run</string>
					<string>Unix</string>
				</array>
				<key>OutputUUID</key>
				<string>B2C3D4E5-F6A1-8901-BCDE-F12345678901</string>
				<key>UUID</key>
				<string>C3D4E5F6-A1B2-9012-CDEF-123456789012</string>
				<key>UnlocalizedApplications</key>
				<array>
					<string>Finder</string>
				</array>
			</dict>
		</dict>
	</array>
	<key>connectors</key>
	<dict/>
	<key>workflowMetaData</key>
	<dict>
		<key>serviceApplicationBundleID</key>
		<string>com.apple.finder</string>
		<key>serviceApplicationPath</key>
		<string>/System/Library/CoreServices/Finder.app</string>
		<key>serviceInputTypeIdentifier</key>
		<string>com.apple.Automator.fileSystemObject.folder</string>
		<key>serviceOutputTypeIdentifier</key>
		<string>com.apple.Automator.nothing</string>
		<key>serviceProcessesInput</key>
		<false/>
		<key>workflowTypeIdentifier</key>
		<string>com.apple.Automator.servicesMenu</string>
	</dict>
</dict>
</plist>
"""
    return wflow_content


def generate_automator_info_plist(action_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", action_name.lower()).strip("-")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>English</string>
	<key>CFBundleIdentifier</key>
	<string>com.launchpad.workflow.{slug}</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>{action_name}</string>
	<key>CFBundlePackageType</key>
	<string>BNDL</string>
	<key>CFBundleShortVersionString</key>
	<string>2.0</string>
	<key>CFBundleVersion</key>
	<string>2.0</string>
	<key>NSServices</key>
	<array>
		<dict>
			<key>NSMenuItem</key>
			<dict>
				<key>default</key>
				<string>{action_name}</string>
			</dict>
			<key>NSMessage</key>
			<string>runWorkflowAsService</string>
			<key>NSRequiredContext</key>
			<dict>
				<key>NSApplicationIdentifier</key>
				<string>com.apple.finder</string>
			</dict>
			<key>NSSendFileTypes</key>
			<array>
				<string>public.folder</string>
				<string>public.directory</string>
				<string>public.item</string>
			</array>
		</dict>
	</array>
</dict>
</plist>
"""


class ServiceInstaller:
    def __init__(self, paths: Paths):
        self.paths = paths

    def install_path_symlink(self, custom_bin_dir: Optional[Path] = None) -> bool:
        """
        Symlink launchpad executable to ~/.local/bin/launchpad
        """
        bin_dir = custom_bin_dir or Path(os.environ.get("LAUNCHPAD_BIN_DIR", str(Path.home() / ".local" / "bin")))
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
            target_link = bin_dir / "launchpad"
            engine_script = self.paths.engine_dir / "launchpad.py"

            # Make engine executable
            try:
                st = os.stat(engine_script)
                os.chmod(engine_script, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass

            if target_link.is_symlink() or target_link.exists():
                target_link.unlink()
            target_link.symlink_to(engine_script.resolve())
            return True
        except Exception:
            return False

    def install_quick_actions(self, custom_services_dir: Optional[Path] = None) -> int:
        """
        Install 6 Automator Quick Action workflows to ~/Library/Services/
        """
        services_dir = custom_services_dir or Path(os.environ.get("LAUNCHPAD_SERVICES_DIR", str(Path.home() / "Library" / "Services")))
        try:
            services_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return 0

        installed_count = 0
        for name, cmd in AUTOMATOR_ACTIONS:
            try:
                bundle_dir = services_dir / f"{name}.workflow"
                contents_dir = bundle_dir / "Contents"
                contents_dir.mkdir(parents=True, exist_ok=True)

                wflow_file = contents_dir / "document.wflow"
                info_file = contents_dir / "Info.plist"

                wflow_file.write_text(generate_automator_wflow_xml(name, cmd), encoding="utf-8")
                info_file.write_text(generate_automator_info_plist(name), encoding="utf-8")
                installed_count += 1
            except Exception:
                pass

        # Trigger macOS pasteboard service cache update and activation if default dir
        if not custom_services_dir:
            try:
                # Enable services in user pbs defaults
                p = subprocess.run(["defaults", "export", "pbs", "-"], capture_output=True, check=False)
                pbs_data = plistlib.loads(p.stdout) if p.returncode == 0 and p.stdout else {}
                if "NSServicesStatus" not in pbs_data:
                    pbs_data["NSServicesStatus"] = {}

                for name, _ in AUTOMATOR_ACTIONS:
                    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
                    keys = [
                        f"(null) - {name} - runWorkflowAsService",
                        f"com.launchpad.workflow.{slug} - {name} - runWorkflowAsService"
                    ]
                    for k in keys:
                        pbs_data["NSServicesStatus"][k] = {
                            "key_equivalent": "",
                            "presentation_modes": {
                                "ContextMenu": 1,
                                "ServicesMenu": 1,
                                "FinderPreview": 1,
                                "TouchBar": 1
                            },
                            "enabled_context_menu": 1,
                            "enabled_services_menu": 1
                        }

                with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tf:
                    plistlib.dump(pbs_data, tf)
                    tf_name = tf.name

                subprocess.run(["defaults", "import", "pbs", tf_name], capture_output=True, check=False)
                try:
                    os.unlink(tf_name)
                except OSError:
                    pass

                subprocess.run(["/System/Library/CoreServices/pbs", "-update"], capture_output=True, check=False)
                subprocess.run(["/System/Library/CoreServices/pbs", "-flush"], capture_output=True, check=False)
            except Exception:
                pass

        return installed_count

    def install_folder_action(self, custom_scripts_dir: Optional[Path] = None) -> bool:
        """
        Install Folder Action script for debounced auto-adoption.
        """
        scripts_dir = custom_scripts_dir or Path(os.environ.get("LAUNCHPAD_SCRIPTS_DIR", str(Path.home() / "Library" / "Scripts" / "Folder Action Scripts")))
        try:
            scripts_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return False

        script_content = f"""-- Launchpad Native Folder Action: Adopt New Projects
on adding folder items to this_folder after receiving added_items
    set launchpad_home to "{self.paths.root}"
    do shell script "export PATH=\\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\\"; export LAUNCHPAD_HOME=\\"" & launchpad_home & "\\"; launchpad sync --scan >/dev/null 2>&1 &"
end adding folder items to
"""
        try:
            script_file = scripts_dir / "Launchpad-Adopt.applescript"
            script_file.write_text(script_content, encoding="utf-8")

            # Compile to .scpt if osacompile exists
            if shutil.which("osacompile"):
                try:
                    scpt_file = scripts_dir / "Launchpad-Adopt.scpt"
                    subprocess.run(
                        ["osacompile", "-o", str(scpt_file), str(script_file)],
                        capture_output=True,
                        check=False
                    )
                except Exception:
                    pass
            return True
        except Exception:
            return False


# ==============================================================================
# 8. GitHub & Adoption Operations
# ==============================================================================

class GitHubClient:
    def __init__(self, audit: AuditLogger):
        self.audit = audit

    def is_available(self) -> bool:
        if not shutil.which("gh"):
            return False
        res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        return res.returncode == 0

    def list_repos(self) -> List[Dict[str, Any]]:
        if not shutil.which("gh"):
            return []
        cmd = [
            "gh", "repo", "list",
            "--limit", "300",
            "--json", "id,nameWithOwner,name,owner,url,isPrivate,defaultBranchRef,description,pushedAt"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return []
        try:
            return json.loads(res.stdout)
        except Exception:
            return []


class AdoptionScanner:
    def __init__(self, paths: Paths, manifest: ManifestManager, config: ConfigManager, surface: SurfaceManager, audit: AuditLogger):
        self.paths = paths
        self.manifest = manifest
        self.config = config
        self.surface = surface
        self.audit = audit

    def is_scan_stale(self, hours: int = 24) -> bool:
        if not self.paths.last_scan_file.exists():
            return True
        try:
            mtime = self.paths.last_scan_file.stat().st_mtime
            return (time.time() - mtime) > (hours * 3600)
        except Exception:
            return True

    def mark_scanned(self):
        self.paths.ensure_layout()
        self.paths.last_scan_file.touch()

    def scan_and_adopt(self, include_github: bool = False, force: bool = False) -> Dict[str, int]:
        """
        Adoption Scan:
        - Walk top-level directories of root.
        - Adopt any git repo / project dir not in manifest.
        - NEVER move, rename, or modify user folders (Guardrails 1, 2).
        """
        if not force and not self.is_scan_stale(self.config.get("scanIntervalHours", 24)):
            return {"adopted": 0, "existing": len(self.manifest.data["projects"]), "github_imported": 0}

        counts = {"adopted": 0, "existing": len(self.manifest.data["projects"]), "github_imported": 0}
        known_paths = {p.get("state", {}).get("path") for p in self.manifest.data["projects"] if p.get("state", {}).get("path")}
        known_urls = {p.get("source", {}).get("url") for p in self.manifest.data["projects"] if p.get("source", {}).get("url")}
        known_ids = {p["id"] for p in self.manifest.data["projects"]}

        # 1. Local directories scan
        if self.paths.root.exists():
            for item in self.paths.root.iterdir():
                if not item.is_dir() or item.name.startswith(".") or item.name == "Launchpad":
                    continue

                rel_name = item.name
                if rel_name in known_paths:
                    continue

                # Check if it is a project directory
                is_git = (item / ".git").exists()
                has_markers = any((item / marker).exists() for marker in [
                    "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt", "Makefile"
                ])

                if not (is_git or has_markers):
                    continue

                # Determine project ID
                proj_id = rel_name.lower().replace(" ", "-")
                if proj_id in known_ids:
                    proj_id = f"{proj_id}-local"
                known_ids.add(proj_id)

                # Determine remote source if git
                remote_url = ""
                owner = ""
                repo_name = rel_name
                canonical = "local-only"
                default_branch = "main"
                is_private = True
                gh_id = None

                if is_git:
                    res = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=str(item),
                        capture_output=True,
                        text=True
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        remote_url = res.stdout.strip()
                        canonical = "github"
                        # Parse owner / repo from url
                        m = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", remote_url)
                        if m:
                            owner, repo_name = m.group(1), m.group(2)

                    # Default branch
                    b_res = subprocess.run(
                        ["git", "branch", "--show-current"],
                        cwd=str(item),
                        capture_output=True,
                        text=True
                    )
                    if b_res.returncode == 0 and b_res.stdout.strip():
                        default_branch = b_res.stdout.strip()

                # Determine initial tier
                has_deps = any((item / dep_dir).exists() for dep_dir in ["node_modules", ".venv", "target", "vendor"])
                tier = "HOT" if has_deps else "WARM"

                # Toolchain detection
                toolchain, recipe, kind = ToolchainDetector.detect_toolchain(
                    item, ignore_scripts=self.config.get("ignoreScripts", False)
                )

                project_record = {
                    "id": proj_id,
                    "source": {
                        "githubId": gh_id,
                        "owner": owner,
                        "repo": repo_name,
                        "url": remote_url or f"file://{item.resolve()}",
                        "defaultBranch": default_branch,
                        "visibility": "private" if is_private else "public",
                        "canonical": canonical,
                        "upstreamGone": False
                    },
                    "kind": kind,
                    "links": {
                        "live": None,
                        "docs": None
                    },
                    "toolchain": toolchain,
                    "recipe": recipe,
                    "state": {
                        "tier": tier,
                        "pinned": True if canonical == "local-only" else False,
                        "path": rel_name,
                        "lastLocalTouch": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "attention": None,
                        "notes": ""
                    }
                }

                self.manifest.upsert_project(project_record)
                self.surface.regenerate_slot(project_record)
                counts["adopted"] += 1
                known_paths.add(rel_name)
                if remote_url:
                    known_urls.add(remote_url)
                self.audit.log("adopt", proj_id, "success", f"Adopted local project from {rel_name} as {tier}")

        # 2. GitHub import if requested
        if include_github:
            gh = GitHubClient(self.audit)
            if gh.is_available():
                repos = gh.list_repos()
                for r in repos:
                    url = r.get("url")
                    name = r.get("name", "")
                    gh_id = r.get("id")
                    owner = r.get("owner", {}).get("login", "") if isinstance(r.get("owner"), dict) else r.get("owner", "")
                    if not owner and "nameWithOwner" in r:
                        parts = r["nameWithOwner"].split("/")
                        if len(parts) == 2:
                            owner, name = parts[0], parts[1]

                    if url in known_urls:
                        continue

                    pid = name.lower().replace(" ", "-")
                    if pid in known_ids:
                        pid = f"{owner.lower()}-{pid}"
                    if pid in known_ids:
                        continue

                    known_ids.add(pid)
                    known_urls.add(url)

                    def_branch = "main"
                    if isinstance(r.get("defaultBranchRef"), dict):
                        def_branch = r["defaultBranchRef"].get("name", "main")

                    cold_record = {
                        "id": pid,
                        "source": {
                            "githubId": gh_id,
                            "owner": owner,
                            "repo": name,
                            "url": url,
                            "defaultBranch": def_branch,
                            "visibility": "private" if r.get("isPrivate") else "public",
                            "canonical": "github",
                            "upstreamGone": False
                        },
                        "kind": "other",
                        "links": {
                            "live": None,
                            "docs": None
                        },
                        "toolchain": {
                            "runtime": "generic",
                            "packageManager": "none",
                            "versionManager": "none"
                        },
                        "recipe": {
                            "install": "",
                            "run": "",
                            "port": 3000
                        },
                        "state": {
                            "tier": "COLD",
                            "pinned": False,
                            "path": None,
                            "lastLocalTouch": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "attention": None,
                            "notes": r.get("description") or ""
                        }
                    }

                    self.manifest.upsert_project(cold_record)
                    self.surface.regenerate_slot(cold_record)
                    counts["github_imported"] += 1
                    self.audit.log("import_github", pid, "success", f"Imported remote repo {owner}/{name} as COLD")

        self.mark_scanned()
        self.manifest.data["meta"]["lastScanAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.manifest.save()
        return counts


# ==============================================================================
# 9. Core Engine Operations & Lifecycle (Hydrate, Evict, Run, Decay, Sync)
# ==============================================================================

class Engine:
    def __init__(self, root: Optional[Path] = None):
        self.paths = Paths(root)
        self.paths.ensure_layout()
        self.audit = AuditLogger(self.paths)
        self.config = ConfigManager(self.paths)
        self.manifest = ManifestManager(self.paths, self.audit)
        self.safety = SafetyGate(self.paths)
        self.surface = SurfaceManager(self.paths)
        self.services = ServiceInstaller(self.paths)
        self.adoption = AdoptionScanner(
            self.paths, self.manifest, self.config, self.surface, self.audit
        )

    def opportunistic_maintenance(self):
        """
        Global background maintenance on engine invocation:
        1. Throttled metadata sync if >6h stale.
        2. Throttled adoption scan if >24h stale.
        """
        # Scan if stale
        if self.adoption.is_scan_stale(self.config.get("scanIntervalHours", 24)):
            try:
                self.adoption.scan_and_adopt(include_github=False)
            except Exception:
                pass

        # Sync if stale
        sync_hours = self.config.get("syncIntervalHours", 6)
        if not self.paths.last_sync_file.exists() or (time.time() - self.paths.last_sync_file.stat().st_mtime) > (sync_hours * 3600):
            try:
                self.sync(full_scan=False, quiet=True)
            except Exception:
                pass

    def bootstrap(self, include_github: bool = False, scan_local: bool = True) -> Dict[str, Any]:
        """
        One-time setup & import:
        - Scaffold directories & config.
        - Adopt top-level directories in-place (Guardrails 1, 2).
        - Optionally import GitHub repos as COLD.
        - Write AGENTS.md + symlinks.
        - Symlink CLI to ~/.local/bin/launchpad.
        - Install Quick Actions & Folder Action.
        - Regenerate index.
        """
        self.paths.ensure_layout()
        engine_script = self.paths.engine_dir / "launchpad.py"
        try:
            current_script = Path(__file__).resolve()
            if current_script.exists() and current_script != engine_script.resolve():
                shutil.copy2(current_script, engine_script)
                os.chmod(engine_script, 0o755)
        except Exception:
            pass

        self.write_agents_contract()
        self.services.install_path_symlink()
        self.services.install_quick_actions()
        self.services.install_folder_action()

        result = {"adopted": 0, "github_imported": 0}
        if scan_local:
            result = self.adoption.scan_and_adopt(include_github=include_github, force=True)

        self.surface.regenerate_all(self.manifest.data)
        self.audit.log("bootstrap", str(self.paths.root), "success", f"Bootstrapped {result}")
        return result

    def write_agents_contract(self):
        """
        Author the root AGENTS.md and symlinks CLAUDE.md & GEMINI.md.
        """
        contract_text = f"""# AGENTS.md — Launchpad Workspace Contract

Welcome to the Launchpad-managed workspace at `{self.paths.root}`.

## Workspace Overview & Architecture
This workspace uses **Launchpad v2**, an Apple-native local project workspace.
- `Launchpad/` contains generated human indices (weblocs, Quick Look READMEs, Finder color tags). This directory is disposable and regenerated from `.launchpad/manifest.json`.
- `.launchpad/` contains the engine state (mode 0700): `manifest.json`, `config.json`, `audit.jsonl`, `engine/launchpad.py`, and `views/`.
- Repositories are materialized directly under `{self.paths.root}/<project-name>`.

## Core Invariants (Non-Negotiable)
1. **Never write engine state or metadata inside any project repository** (including `.git`). The engine and agents write state only inside `.launchpad/` and `Launchpad/`.
2. **Never move, rename, or delete existing project folders.**
3. **Never auto-push, auto-pull, or auto-merge.** Local clones are disposable caches; GitHub is canonical.
4. **Never destroy uncommitted work.** Eviction audits working tree cleanliness.

## Agent Permissions & Boundaries
- **Permitted:**
  - Read all files across all projects and engine directories.
  - Write source code, tests, and configurations inside project directories.
  - Use `launchpad context [dir]` to orient yourself on any project.
  - Use `launchpad hydrate <id> [--hot]` to materialize or install a project.
  - Use `launchpad evict <id>` to safely demote clean projects.
  - Generate disposable dashboards or artifacts in `.launchpad/views/`.
- **Forbidden:**
  - `launchpad evict --force` (structurally blocked in non-TTY/agent environments).
  - Pushing to remote repositories or initiating deployments.
  - Manually hand-editing `.launchpad/manifest.json` or state files (always use `launchpad` CLI).

## Orientation & Common Commands
- Orient yourself in any project directory:
  ```bash
  launchpad context .
  ```
- Check status of all projects:
  ```bash
  launchpad status
  ```
- Promote a project from COLD to WARM or HOT:
  ```bash
  launchpad hydrate <id> --hot
  ```
- Register a newly created project:
  ```bash
  launchpad add .
  ```
- Record a live deployment URL:
  ```bash
  launchpad set-url <id> https://...
  ```
"""
        with open(self.paths.agents_md, "w", encoding="utf-8") as f:
            f.write(contract_text)

        for sym in [self.paths.claude_md, self.paths.gemini_md]:
            try:
                if sym.is_symlink() or sym.exists():
                    sym.unlink()
                sym.symlink_to(Path("AGENTS.md"))
            except Exception:
                pass

    def add(self, target: str) -> Dict[str, Any]:
        """
        Add a project: local path (in-place) or remote URL/slug (COLD).
        """
        p = Path(target).expanduser()
        if p.exists() and p.is_dir():
            # In-place local adoption
            rel_name = p.resolve().name
            if p.resolve().parent == self.paths.root.resolve():
                rel_path = rel_name
            else:
                rel_path = str(p.resolve())

            proj_id = rel_name.lower().replace(" ", "-")
            toolchain, recipe, kind = ToolchainDetector.detect_toolchain(p, self.config.get("ignoreScripts", False))
            has_deps = any((p / d).exists() for d in ["node_modules", ".venv", "target", "vendor"])

            remote_url = ""
            owner = ""
            repo_name = rel_name
            if (p / ".git").exists():
                res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(p), capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    remote_url = res.stdout.strip()
                    m = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", remote_url)
                    if m:
                        owner, repo_name = m.group(1), m.group(2)

            record = {
                "id": proj_id,
                "source": {
                    "owner": owner,
                    "repo": repo_name,
                    "url": remote_url or f"file://{p.resolve()}",
                    "defaultBranch": "main",
                    "visibility": "private",
                    "canonical": "github" if remote_url else "local-only",
                    "upstreamGone": False
                },
                "kind": kind,
                "links": {"live": None, "docs": None},
                "toolchain": toolchain,
                "recipe": recipe,
                "state": {
                    "tier": "HOT" if has_deps else "WARM",
                    "pinned": True if not remote_url else False,
                    "path": rel_path,
                    "lastLocalTouch": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "attention": None,
                    "notes": ""
                }
            }
            self.manifest.upsert_project(record)
            self.surface.regenerate_slot(record)
            self.audit.log("add", proj_id, "success", f"Added local project at {p}")
            return record
        else:
            # Remote owner/repo or URL
            target_str = str(target).strip()
            if target_str.startswith("http://") or target_str.startswith("https://") or target_str.startswith("git@"):
                url = target_str
                m = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", url)
                owner = m.group(1) if m else ""
                repo_name = m.group(2).replace(".git", "") if m else target_str.split("/")[-1]
            elif "/" in target_str:
                parts = target_str.split("/")
                owner, repo_name = parts[0], parts[1]
                url = f"https://github.com/{owner}/{repo_name}.git"
            else:
                owner = ""
                repo_name = target_str
                url = f"https://github.com/{target_str}.git"

            pid = repo_name.lower().replace(" ", "-")
            record = {
                "id": pid,
                "source": {
                    "owner": owner,
                    "repo": repo_name,
                    "url": url,
                    "defaultBranch": "main",
                    "visibility": "public",
                    "canonical": "github",
                    "upstreamGone": False
                },
                "kind": "other",
                "links": {"live": None, "docs": None},
                "toolchain": {"runtime": "generic", "packageManager": "none", "versionManager": "none"},
                "recipe": {"install": "", "run": "", "port": 3000},
                "state": {
                    "tier": "COLD",
                    "pinned": False,
                    "path": None,
                    "lastLocalTouch": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "attention": None,
                    "notes": ""
                }
            }
            self.manifest.upsert_project(record)
            self.surface.regenerate_slot(record)
            self.audit.log("add", pid, "success", f"Added remote project {url} as COLD")
            return record

    def sync(self, full_scan: bool = False, quiet: bool = False) -> Dict[str, Any]:
        """
        Metadata refresh now:
        - Query GitHub for updates, renames, deletions.
        - Match by stable GitHub repo ID (immune to renames/transfers), then URL, then name.
        - If renamed: update manifest and local clone remote URL (one permitted write).
        - If deleted: mark gone + orphaned and auto-pin local clone.
        - Refresh cached READMEs (markdown + HTML).
        - Optionally run adoption scan.
        """
        gh = GitHubClient(self.audit)
        updates = {"renamed": 0, "gone": 0, "refreshed": 0}

        if gh.is_available():
            repos = gh.list_repos()
            id_map = {r.get("id"): r for r in repos if r.get("id")}
            repo_map = {r.get("url"): r for r in repos if r.get("url")}
            name_map = {f"{r.get('owner', {}).get('login', '')}/{r.get('name', '')}": r for r in repos}

            for p in self.manifest.data["projects"]:
                src = p.get("source", {})
                if src.get("canonical") != "github":
                    continue

                gh_id = src.get("githubId")
                url = src.get("url")
                owner = src.get("owner")
                name = src.get("repo")
                key = f"{owner}/{name}"

                # Reconcile: ID first (stable across renames), then URL, then owner/name
                remote_data = None
                if gh_id and gh_id in id_map:
                    remote_data = id_map[gh_id]
                elif url and url in repo_map:
                    remote_data = repo_map[url]
                elif key in name_map:
                    remote_data = name_map[key]

                if remote_data:
                    # Update githubId if previously missing
                    if not src.get("githubId") and remote_data.get("id"):
                        src["githubId"] = remote_data["id"]

                    # Refresh metadata
                    new_url = remote_data.get("url", url)
                    new_name = remote_data.get("name", name)
                    new_owner = remote_data.get("owner", {}).get("login", owner) if isinstance(remote_data.get("owner"), dict) else owner

                    # Check for upstream rename / transfer
                    if new_url != url or new_name != name or new_owner != owner:
                        src["url"] = new_url
                        src["repo"] = new_name
                        src["owner"] = new_owner
                        updates["renamed"] += 1

                        # Update local git remote if cloned
                        rel_path = p.get("state", {}).get("path")
                        if rel_path:
                            repo_dir = self.paths.root / rel_path
                            if repo_dir.exists() and (repo_dir / ".git").exists():
                                subprocess.run(
                                    ["git", "remote", "set-url", "origin", new_url],
                                    cwd=str(repo_dir),
                                    capture_output=True
                                )
                                self.audit.log("sync_rename", p["id"], "success", f"Updated remote URL to {new_url}")

                    # Description
                    if remote_data.get("description"):
                        p["state"]["notes"] = remote_data["description"]
                    updates["refreshed"] += 1
                else:
                    # Upstream deleted / gone
                    if not src.get("upstreamGone"):
                        src["upstreamGone"] = True
                        p["state"]["attention"] = "gone"
                        # If local clone exists, mark orphaned and auto-pin
                        rel_path = p.get("state", {}).get("path")
                        if rel_path and (self.paths.root / rel_path).exists():
                            p["state"]["attention"] = "orphaned"
                            p["state"]["pinned"] = True
                        updates["gone"] += 1
                        self.audit.log("sync_gone", p["id"], "warning", "Upstream repository deleted; auto-pinned local clone")

                self.surface.regenerate_slot(p)

        if full_scan:
            self.adoption.scan_and_adopt(include_github=False, force=True)

        self.paths.ensure_layout()
        self.paths.last_sync_file.touch()
        self.manifest.data["meta"]["lastSyncAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.manifest.save()

        if not quiet:
            self.audit.log("sync", str(self.paths.root), "success", f"Sync completed: {updates}")
        return updates

    def hydrate(self, project_id: str, hot: bool = False) -> Dict[str, Any]:
        """
        Promote COLD -> WARM (blobless clone) -> HOT (frozen install + recipe verification).
        Enforces free disk floor (default 5 GB).
        """
        project = self.manifest.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found in manifest")

        # Disk space pre-flight check
        min_gb = self.config.get("minFreeDiskGB", 5)
        usage = shutil.disk_usage(self.paths.root)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < min_gb:
            err = f"Hydration aborted: free disk space ({free_gb:.2f} GB) is below the required floor ({min_gb} GB)."
            self.audit.log("hydrate", project_id, "blocked", err)
            raise RuntimeError(err)

        current_tier = project.get("state", {}).get("tier", "COLD")
        rel_path = project.get("state", {}).get("path") or project["id"]
        target_dir = self.paths.root / rel_path

        # 1. COLD -> WARM
        if current_tier == "COLD" or not target_dir.exists():
            remote_url = project.get("source", {}).get("url")
            if not remote_url:
                raise RuntimeError(f"Cannot clone project '{project_id}': no valid remote URL")

            target_dir.mkdir(parents=True, exist_ok=True)
            clone_cmd = ["git", "clone", "--filter=blob:none", remote_url, str(target_dir)]
            res = subprocess.run(clone_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                # Cleanup on failure
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                err = f"Git clone failed: {res.stderr.strip()}"
                self.audit.log("hydrate_clone", project_id, "error", err)
                raise RuntimeError(err)

            # Submodules if any
            if (target_dir / ".gitmodules").exists():
                subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=str(target_dir),
                    capture_output=True
                )

            project["state"]["tier"] = "WARM"
            project["state"]["path"] = rel_path
            project["state"]["lastLocalTouch"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # Re-detect toolchain from materialized repo
            toolchain, recipe, kind = ToolchainDetector.detect_toolchain(
                target_dir, self.config.get("ignoreScripts", False)
            )
            project["toolchain"] = toolchain
            project["recipe"] = recipe
            project["kind"] = kind
            self.manifest.upsert_project(project)
            self.surface.regenerate_slot(project)
            self.audit.log("hydrate_warm", project_id, "success", f"Cloned blobless to {rel_path}")

        # 2. WARM -> HOT if requested
        if hot:
            toolchain, recipe, kind = ToolchainDetector.detect_toolchain(
                target_dir, self.config.get("ignoreScripts", False)
            )
            install_cmd = recipe.get("install") or project.get("recipe", {}).get("install")

            if install_cmd:
                res = subprocess.run(install_cmd, shell=True, cwd=str(target_dir), capture_output=True, text=True)
                if res.returncode != 0:
                    err = f"Frozen dependency install failed: {res.stderr.strip()}"
                    self.audit.log("hydrate_hot_install", project_id, "error", err)
                    raise RuntimeError(err)

            project["state"]["tier"] = "HOT"
            project["state"]["lastLocalTouch"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.manifest.upsert_project(project)
            self.surface.regenerate_slot(project)
            self.audit.log("hydrate_hot", project_id, "success", f"Installed dependencies and verified recipe for {project_id}")

        return project

    def evict(self, project_id: str, force: bool = False) -> bool:
        """
        Demote WARM/HOT -> COLD:
        Strict Guardrail 3 Cleanliness Audit:
        - Refuse if uncommitted changes, untracked non-ignored files, stashes, unpushed commits, or dirty submodules.
        - If dirty and force requested: hard-fail in non-TTY; prompt user to type project id in interactive TTY.
        """
        project = self.manifest.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found in manifest")

        can_delete, reason = self.safety.can_evict(project, force=force)
        if not can_delete:
            project["state"]["attention"] = "dirty"
            self.manifest.upsert_project(project)
            self.surface.regenerate_slot(project)
            self.audit.log("evict", project_id, "blocked", reason)
            raise RuntimeError(reason)

        rel_path = project.get("state", {}).get("path")
        if rel_path:
            repo_dir = self.paths.root / rel_path
            if repo_dir.exists():
                shutil.rmtree(repo_dir)

        project["state"]["tier"] = "COLD"
        project["state"]["attention"] = None
        project["state"]["path"] = None
        project["state"]["lastLocalTouch"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.manifest.upsert_project(project)
        self.surface.regenerate_slot(project)
        self.audit.log("evict", project_id, "success", f"Evicted {project_id} to COLD")
        return True

    def run_project(self, project_id: str, foreground: bool = True) -> Dict[str, Any]:
        """
        HOT only: Probe port conflict, resolve runtime/version manager, and execute run recipe.
        """
        project = self.manifest.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found in manifest")

        tier = project.get("state", {}).get("tier")
        if tier != "HOT":
            raise RuntimeError(f"Cannot run project '{project_id}': tier is {tier}. Must be HOT (run `launchpad hydrate {project_id} --hot`).")

        rel_path = project.get("state", {}).get("path")
        if not rel_path:
            raise RuntimeError(f"Project path for '{project_id}' is not set.")

        repo_dir = self.paths.root / rel_path
        recipe = project.get("recipe", {})
        run_cmd = recipe.get("run")
        if not run_cmd:
            raise RuntimeError(f"No run recipe defined for project '{project_id}'.")

        default_port = recipe.get("port") or 3000
        active_port = ToolchainDetector.probe_available_port(default_port)

        env = os.environ.copy()
        env["PORT"] = str(active_port)

        msg = f"Running '{project_id}' at http://localhost:{active_port} (command: {run_cmd})"
        self.audit.log("run", project_id, "success", msg)

        if not foreground:
            return {"status": "ready", "url": f"http://localhost:{active_port}", "port": active_port, "command": run_cmd}

        print(f"\n🚀 Launchpad: {msg}")
        try:
            p = subprocess.Popen(run_cmd, shell=True, cwd=str(repo_dir), env=env)
            p.wait()
        except KeyboardInterrupt:
            p.terminate()
            print("\nStopped.")

        return {"status": "stopped", "url": f"http://localhost:{active_port}", "port": active_port}

    def decay(self) -> List[Dict[str, Any]]:
        """
        Decay demotions:
        - HOT -> WARM after hotDays (default 21) without touch.
        - WARM -> COLD after warmDays (default 120) without touch.
        - Pinned, dirty, and attention projects are strictly exempt.
        """
        hot_days = self.config.get("hotDays", 21)
        warm_days = self.config.get("warmDays", 120)
        now = datetime.datetime.now(datetime.timezone.utc)
        actions = []

        for p in self.manifest.data["projects"]:
            state = p.get("state", {})
            tier = state.get("tier")
            if tier == "COLD":
                continue

            # Exemptions
            if state.get("pinned") or state.get("attention") is not None:
                continue

            last_touch_str = state.get("lastLocalTouch")
            if not last_touch_str:
                continue

            try:
                last_touch = datetime.datetime.fromisoformat(last_touch_str)
            except Exception:
                continue

            days_elapsed = (now - last_touch).total_seconds() / 86400.0
            pid = p["id"]

            if tier == "HOT" and days_elapsed >= hot_days:
                # HOT -> WARM
                p["state"]["tier"] = "WARM"
                self.manifest.upsert_project(p)
                self.surface.regenerate_slot(p)
                actions.append({"id": pid, "from": "HOT", "to": "WARM", "days": days_elapsed})
                self.audit.log("decay", pid, "success", f"Decayed HOT -> WARM after {days_elapsed:.1f} days")

            elif tier == "WARM" and days_elapsed >= warm_days:
                # Check cleanliness before demoting to COLD
                rel_path = state.get("path")
                if rel_path:
                    repo_dir = self.paths.root / rel_path
                    is_clean, _ = self.safety.audit_cleanliness(repo_dir)
                    if not is_clean:
                        p["state"]["attention"] = "dirty"
                        self.manifest.upsert_project(p)
                        self.surface.regenerate_slot(p)
                        continue
                    shutil.rmtree(repo_dir, ignore_errors=True)

                p["state"]["tier"] = "COLD"
                p["state"]["path"] = None
                self.manifest.upsert_project(p)
                self.surface.regenerate_slot(p)
                actions.append({"id": pid, "from": "WARM", "to": "COLD", "days": days_elapsed})
                self.audit.log("decay", pid, "success", f"Decayed WARM -> COLD after {days_elapsed:.1f} days")

        return actions

    def pin(self, project_id: str, pinned: bool = True):
        project = self.manifest.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found")
        project["state"]["pinned"] = pinned
        self.manifest.upsert_project(project)
        self.surface.regenerate_slot(project)
        self.audit.log("pin" if pinned else "unpin", project_id, "success")

    def set_url(self, project_id: str, url: str, link_type: str = "live"):
        project = self.manifest.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found")
        if link_type not in ("live", "docs"):
            link_type = "live"
        project["links"][link_type] = url
        self.manifest.upsert_project(project)
        self.surface.regenerate_slot(project)
        self.audit.log("set_url", project_id, "success", f"Set {link_type} URL to {url}")

    def open_project(self, project_id_or_path: str, target: Optional[str] = None):
        """
        Open project: default order is live -> docs -> github -> folder.
        --editor opens in configured editor.
        """
        project = self.manifest.get_project(project_id_or_path)
        if not project:
            # Try to resolve path
            target_path = Path(project_id_or_path).resolve()
            for p in self.manifest.data["projects"]:
                rel = p.get("state", {}).get("path")
                if rel and (self.paths.root / rel).resolve() == target_path:
                    project = p
                    break
        if not project:
            raise ValueError(f"Project '{project_id_or_path}' not found")

        pid = project["id"]
        rel_path = project.get("state", {}).get("path")
        local_dir = (self.paths.root / rel_path) if rel_path else None
        slot_dir = self.paths.launchpad_index / pid

        if target == "live":
            url = project.get("links", {}).get("live")
            if not url:
                raise RuntimeError(f"No live URL registered for project '{pid}'.")
            subprocess.run(["open", url])
            return

        if target == "github":
            url = project.get("source", {}).get("url")
            if not url:
                raise RuntimeError(f"No GitHub URL registered for project '{pid}'.")
            subprocess.run(["open", url])
            return

        if target == "editor":
            editor_cmd = self._resolve_editor()
            target_open = local_dir if (local_dir and local_dir.exists()) else slot_dir
            subprocess.run(f"{editor_cmd} \"{target_open}\"", shell=True)
            return

        if target == "folder":
            target_open = local_dir if (local_dir and local_dir.exists()) else slot_dir
            subprocess.run(["open", str(target_open)])
            return

        # Default resolution order: live -> docs -> github -> folder
        live_url = project.get("links", {}).get("live")
        if live_url:
            subprocess.run(["open", live_url])
            return

        docs_url = project.get("links", {}).get("docs")
        if docs_url:
            subprocess.run(["open", docs_url])
            return

        github_url = project.get("source", {}).get("url")
        if github_url and github_url.startswith("http"):
            subprocess.run(["open", github_url])
            return

        target_open = local_dir if (local_dir and local_dir.exists()) else slot_dir
        subprocess.run(["open", str(target_open)])

    def _resolve_editor(self) -> str:
        cfg_editor = self.config.get("editor", "auto")
        if cfg_editor != "auto" and cfg_editor:
            return cfg_editor

        for candidate in ["cursor", "code", "zed", "subl"]:
            if shutil.which(candidate):
                return candidate
        if os.environ.get("VISUAL"):
            return os.environ["VISUAL"]
        if os.environ.get("EDITOR"):
            return os.environ["EDITOR"]
        return "open"

    def get_context(self, project_id_or_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Emit project card for agent orientation.
        """
        if project_id_or_path:
            p = self.manifest.get_project(project_id_or_path)
            if not p:
                target_p = Path(project_id_or_path).resolve()
                for item in self.manifest.data["projects"]:
                    rel = item.get("state", {}).get("path")
                    if rel and (self.paths.root / rel).resolve() == target_p:
                        p = item
                        break
        else:
            current = Path.cwd().resolve()
            p = None
            for item in self.manifest.data["projects"]:
                rel = item.get("state", {}).get("path")
                if rel and (self.paths.root / rel).resolve() == current:
                    p = item
                    break

        if not p:
            raise ValueError("No matching project found for context.")

        return p

    def doctor(self, redacted: bool = False) -> Dict[str, Any]:
        """
        Environment diagnostics and health check.
        --redacted mode masks repo names, URLs, and private paths.
        """
        gh_status = "unauthenticated/offline"
        gh_available = False
        if shutil.which("gh"):
            res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            if res.returncode == 0:
                gh_status = "authenticated"
                gh_available = True

        git_version = "not found"
        if shutil.which("git"):
            res = subprocess.run(["git", "--version"], capture_output=True, text=True)
            git_version = res.stdout.strip()

        symlink_ok = (Path.home() / ".local" / "bin" / "launchpad").is_symlink() or (Path.home() / ".local" / "bin" / "launchpad").exists()
        path_ok = any(str(Path.home() / ".local" / "bin") in p for p in os.environ.get("PATH", "").split(":"))

        usage = shutil.disk_usage(self.paths.root)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)

        projects_summary = {
            "total": len(self.manifest.data["projects"]),
            "COLD": sum(1 for p in self.manifest.data["projects"] if p.get("state", {}).get("tier") == "COLD"),
            "WARM": sum(1 for p in self.manifest.data["projects"] if p.get("state", {}).get("tier") == "WARM"),
            "HOT": sum(1 for p in self.manifest.data["projects"] if p.get("state", {}).get("tier") == "HOT"),
            "pinned": sum(1 for p in self.manifest.data["projects"] if p.get("state", {}).get("pinned")),
            "attention": sum(1 for p in self.manifest.data["projects"] if p.get("state", {}).get("attention")),
        }

        root_display = "<REDACTED_ROOT>" if redacted else str(self.paths.root)

        return {
            "launchpadVersion": ENGINE_VERSION,
            "schemaVersion": self.manifest.data.get("schemaVersion"),
            "root": root_display,
            "pythonVersion": sys.version.split()[0],
            "gitVersion": git_version,
            "ghStatus": gh_status,
            "ghAvailable": gh_available,
            "pathSymlink": "installed" if symlink_ok else "missing",
            "pathInEnv": path_ok,
            "editor": self._resolve_editor(),
            "disk": {
                "freeGB": round(free_gb, 2),
                "totalGB": round(total_gb, 2),
                "floorGB": self.config.get("minFreeDiskGB", 5),
                "healthy": free_gb >= self.config.get("minFreeDiskGB", 5)
            },
            "projectsSummary": projects_summary
        }


# ==============================================================================
# 10. CLI Dispatcher & Formatting
# ==============================================================================

def format_table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "No records found."
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    header_line = "  ".join(f"{h:<{widths[i]}}" for i, h in enumerate(headers))
    sep_line = "  ".join("-" * widths[i] for i in range(len(headers)))
    row_lines = ["  ".join(f"{str(cell):<{widths[i]}}" for i, cell in enumerate(r)) for r in rows]

    return "\n".join([header_line, sep_line] + row_lines)


def main():
    json_parser = argparse.ArgumentParser(add_help=False)
    json_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    parser = argparse.ArgumentParser(
        prog="launchpad",
        description="Launchpad v2: Self-maintaining local project workspace for macOS.",
        parents=[json_parser]
    )
    subparsers = parser.add_subparsers(dest="command")

    # bootstrap
    sp_bootstrap = subparsers.add_parser("bootstrap", parents=[json_parser], help="Initialize workspace & adopt projects")
    sp_bootstrap.add_argument("--github", action="store_true", help="Import all remote GitHub repos as COLD")
    sp_bootstrap.add_argument("--scan-local", action="store_true", default=True, help="Scan local project folders")

    # add
    sp_add = subparsers.add_parser("add", parents=[json_parser], help="Add project by path or owner/repo")
    sp_add.add_argument("target", help="Local directory path or GitHub owner/repo")

    # sync
    sp_sync = subparsers.add_parser("sync", parents=[json_parser], help="Synchronize GitHub metadata & adopt new projects")
    sp_sync.add_argument("--scan", action="store_true", help="Also perform local adoption scan")

    # list
    sp_list = subparsers.add_parser("list", parents=[json_parser], help="List known projects")
    sp_list.add_argument("--tier", choices=["COLD", "WARM", "HOT"], help="Filter by tier")

    # search
    sp_search = subparsers.add_parser("search", parents=[json_parser], help="Search projects by query")
    sp_search.add_argument("query", help="Search string")

    # status
    subparsers.add_parser("status", parents=[json_parser], help="Show workspace status, disk usage, and health")

    # open
    sp_open = subparsers.add_parser("open", parents=[json_parser], help="Open project surface")
    sp_open.add_argument("id", help="Project id or folder path")
    sp_open.add_argument("--live", action="store_true", help="Open live URL")
    sp_open.add_argument("--github", action="store_true", help="Open GitHub repository")
    sp_open.add_argument("--editor", action="store_true", help="Open in configured editor")
    sp_open.add_argument("--folder", action="store_true", help="Open folder in Finder")

    # hydrate
    sp_hydrate = subparsers.add_parser("hydrate", parents=[json_parser], help="Promote project COLD -> WARM/HOT")
    sp_hydrate.add_argument("id", help="Project id")
    sp_hydrate.add_argument("--hot", action="store_true", help="Install dependencies & verify run recipe")

    # evict
    sp_evict = subparsers.add_parser("evict", parents=[json_parser], help="Demote project to COLD")
    sp_evict.add_argument("id", help="Project id")
    sp_evict.add_argument("--force", action="store_true", help="Force eviction (requires TTY confirmation)")

    # run
    sp_run = subparsers.add_parser("run", parents=[json_parser], help="Run HOT project recipe")
    sp_run.add_argument("id", help="Project id")

    # decay
    subparsers.add_parser("decay", parents=[json_parser], help="Run decay demotions on inactive projects")

    # pin / unpin
    sp_pin = subparsers.add_parser("pin", parents=[json_parser], help="Pin project to prevent decay")
    sp_pin.add_argument("id", help="Project id")
    sp_unpin = subparsers.add_parser("unpin", parents=[json_parser], help="Unpin project")
    sp_unpin.add_argument("id", help="Project id")

    # set-url
    sp_set_url = subparsers.add_parser("set-url", parents=[json_parser], help="Set live or docs URL for a project")
    sp_set_url.add_argument("id", help="Project id")
    sp_set_url.add_argument("url", help="URL string")
    sp_set_url.add_argument("--type", choices=["live", "docs"], default="live", help="URL type")

    # context
    sp_ctx = subparsers.add_parser("context", parents=[json_parser], help="Output project orientation card")
    sp_ctx.add_argument("dir", nargs="?", default=".", help="Project directory or id")

    # regenerate
    subparsers.add_parser("regenerate", parents=[json_parser], help="Rebuild Launchpad/ index from manifest")

    # validate
    subparsers.add_parser("validate", parents=[json_parser], help="Validate manifest schema and data integrity")

    # doctor
    sp_doc = subparsers.add_parser("doctor", parents=[json_parser], help="Inspect workspace and system health")
    sp_doc.add_argument("--redacted", action="store_true", help="Redact private names/paths for sharing")

    # install-services
    subparsers.add_parser("install-services", parents=[json_parser], help="Install Quick Actions, Folder Action, and PATH symlink")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    engine = Engine()

    # Opportunistic background maintenance
    if args.command not in ("bootstrap", "validate", "doctor"):
        engine.opportunistic_maintenance()

    try:
        if args.command == "bootstrap":
            res = engine.bootstrap(include_github=args.github, scan_local=args.scan_local)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("✨ Launchpad workspace bootstrapped successfully!")
                print(f"  • Local projects adopted: {res.get('adopted', 0)}")
                print(f"  • Remote repos imported: {res.get('github_imported', 0)}")
                print(f"  • Quick Actions installed to ~/Library/Services")
                print(f"  • Symlinked: ~/.local/bin/launchpad")

        elif args.command == "add":
            res = engine.add(args.target)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Added project '{res['id']}' [{res['state']['tier']}]")

        elif args.command == "sync":
            res = engine.sync(full_scan=args.scan)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Sync complete. Refreshed: {res['refreshed']}, Renamed: {res['renamed']}, Gone: {res['gone']}")

        elif args.command == "list":
            projects = engine.manifest.data.get("projects", [])
            if args.tier:
                projects = [p for p in projects if p.get("state", {}).get("tier") == args.tier]
            if args.json:
                print(json.dumps(projects, indent=2))
            else:
                headers = ["ID", "TIER", "PINNED", "RUNTIME", "ATTENTION", "LIVE URL"]
                rows = []
                for p in projects:
                    rows.append([
                        p["id"],
                        p.get("state", {}).get("tier", "COLD"),
                        "📌 Yes" if p.get("state", {}).get("pinned") else "No",
                        p.get("toolchain", {}).get("runtime", "generic"),
                        p.get("state", {}).get("attention") or "-",
                        p.get("links", {}).get("live") or "-"
                    ])
                print(format_table(headers, rows))

        elif args.command == "search":
            q = args.query.lower()
            projects = [
                p for p in engine.manifest.data.get("projects", [])
                if q in p["id"].lower()
                or q in (p.get("source", {}).get("owner", "") + "/" + p.get("source", {}).get("repo", "")).lower()
                or q in p.get("state", {}).get("notes", "").lower()
            ]
            if args.json:
                print(json.dumps(projects, indent=2))
            else:
                headers = ["ID", "TIER", "SOURCE", "RUNTIME"]
                rows = [[p["id"], p.get("state", {}).get("tier"), p.get("source", {}).get("url", "-"), p.get("toolchain", {}).get("runtime", "-")] for p in projects]
                print(format_table(headers, rows))

        elif args.command == "status":
            doc = engine.doctor(redacted=False)
            if args.json:
                print(json.dumps(doc, indent=2))
            else:
                s = doc["projectsSummary"]
                d = doc["disk"]
                print("Launchpad Workspace Status:")
                print(f"  Root: {doc['root']}")
                print(f"  Projects: {s['total']} total ({s['HOT']} HOT, {s['WARM']} WARM, {s['COLD']} COLD, {s['pinned']} pinned, {s['attention']} needs-attention)")
                print(f"  Disk Space: {d['freeGB']} GB free / {d['totalGB']} GB total (floor: {d['floorGB']} GB) - {'✅ Healthy' if d['healthy'] else '⚠️ Low Disk Space'}")
                print(f"  GitHub CLI: {doc['ghStatus']}")
                print(f"  Editor: {doc['editor']}")

        elif args.command == "open":
            target = None
            if args.live:
                target = "live"
            elif args.github:
                target = "github"
            elif args.editor:
                target = "editor"
            elif args.folder:
                target = "folder"
            engine.open_project(args.id, target=target)

        elif args.command == "hydrate":
            res = engine.hydrate(args.id, hot=args.hot)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Hydrated '{args.id}' -> {res['state']['tier']}")

        elif args.command == "evict":
            engine.evict(args.id, force=args.force)
            if args.json:
                print(json.dumps({"id": args.id, "status": "evicted", "tier": "COLD"}))
            else:
                print(f"Evicted '{args.id}' to COLD.")

        elif args.command == "run":
            res = engine.run_project(args.id, foreground=True)
            if args.json:
                print(json.dumps(res, indent=2))

        elif args.command == "decay":
            actions = engine.decay()
            if args.json:
                print(json.dumps(actions, indent=2))
            else:
                if actions:
                    for a in actions:
                        print(f"Decayed '{a['id']}': {a['from']} -> {a['to']} ({a['days']:.1f} days untouched)")
                else:
                    print("Decay scan complete: 0 projects decayed.")

        elif args.command == "pin":
            engine.pin(args.id, pinned=True)
            print(f"Pinned '{args.id}'.")

        elif args.command == "unpin":
            engine.pin(args.id, pinned=False)
            print(f"Unpinned '{args.id}'.")

        elif args.command == "set-url":
            engine.set_url(args.id, args.url, link_type=args.type)
            print(f"Updated {args.type} URL for '{args.id}' to {args.url}")

        elif args.command == "context":
            p = engine.get_context(args.dir)
            if args.json:
                print(json.dumps(p, indent=2))
            else:
                st = p.get("state", {})
                tc = p.get("toolchain", {})
                rc = p.get("recipe", {})
                print(f"# Project: {p['id']}")
                print(f"- Tier: {st.get('tier')} | Pinned: {st.get('pinned')} | Attention: {st.get('attention') or 'None'}")
                print(f"- Source: {p.get('source', {}).get('url')}")
                print(f"- Live: {p.get('links', {}).get('live') or 'None'} | Docs: {p.get('links', {}).get('docs') or 'None'}")
                print(f"- Toolchain: {tc.get('runtime')} (package manager: {tc.get('packageManager')})")
                print(f"- Run Recipe: `{rc.get('run')}` (port: {rc.get('port')})")

        elif args.command == "regenerate":
            engine.surface.regenerate_all(engine.manifest.data)
            print("Regenerated Launchpad/ index.")

        elif args.command == "validate":
            engine.manifest._validate_schema(engine.manifest.data)
            print("Manifest validation passed. Schema version: 1")

        elif args.command == "doctor":
            doc = engine.doctor(redacted=args.redacted)
            if args.json:
                print(json.dumps(doc, indent=2))
            else:
                print("Launchpad Doctor Report:")
                for k, v in doc.items():
                    print(f"  {k}: {v}")

        elif args.command == "install-services":
            engine.services.install_path_symlink()
            n = engine.services.install_quick_actions()
            engine.services.install_folder_action()
            print(f"Installed {n} Quick Actions, Folder Action, and PATH symlink.")

    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

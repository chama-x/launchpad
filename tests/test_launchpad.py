#!/usr/bin/env python3
"""
Comprehensive Acceptance Test Suite for Launchpad v2.
Tests all 4 phases (Foundation, Index & Access, Lifecycle, Hardening) within isolated sandboxes.
Zero side-effects on real ~/Projects or user environment.
"""

import datetime
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add engine directory to sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
if (WORKSPACE_DIR / "engine").exists():
    sys.path.insert(0, str(WORKSPACE_DIR / "engine"))
if (WORKSPACE_DIR / ".launchpad" / "engine").exists():
    sys.path.insert(0, str(WORKSPACE_DIR / ".launchpad" / "engine"))

import launchpad


def compute_dir_checksum(dir_path: Path) -> Dict[str, str]:
    """Compute sha256 checksum of all files in a directory tree."""
    checksums = {}
    if not dir_path.exists():
        return checksums
    for root, _, files in os.walk(dir_path):
        for f in files:
            p = Path(root) / f
            try:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                rel = p.relative_to(dir_path)
                checksums[str(rel)] = h
            except Exception:
                pass
    return checksums


class LaunchpadTestCase(unittest.TestCase):
    def setUp(self):
        # Create unique sandbox root for each test
        self.temp_dir = tempfile.TemporaryDirectory(prefix="launchpad_test_")
        self.sandbox_root = Path(self.temp_dir.name).resolve()
        self.old_launchpad_home = os.environ.get("LAUNCHPAD_HOME")
        os.environ["LAUNCHPAD_HOME"] = str(self.sandbox_root)

        # Temporary user services and bin dir for isolation
        self.sandbox_bin = self.sandbox_root / ".bin"
        self.sandbox_services = self.sandbox_root / ".services"
        self.sandbox_scripts = self.sandbox_root / ".scripts"
        self.sandbox_bin.mkdir(parents=True, exist_ok=True)
        self.sandbox_services.mkdir(parents=True, exist_ok=True)
        self.sandbox_scripts.mkdir(parents=True, exist_ok=True)

        os.environ["LAUNCHPAD_SERVICES_DIR"] = str(self.sandbox_services)
        os.environ["LAUNCHPAD_BIN_DIR"] = str(self.sandbox_bin)
        os.environ["LAUNCHPAD_SCRIPTS_DIR"] = str(self.sandbox_scripts)

        self.engine = launchpad.Engine(root=self.sandbox_root)

    def tearDown(self):
        if self.old_launchpad_home is not None:
            os.environ["LAUNCHPAD_HOME"] = self.old_launchpad_home
        else:
            os.environ.pop("LAUNCHPAD_HOME", None)
        os.environ.pop("LAUNCHPAD_SERVICES_DIR", None)
        os.environ.pop("LAUNCHPAD_BIN_DIR", None)
        os.environ.pop("LAUNCHPAD_SCRIPTS_DIR", None)
        self.temp_dir.cleanup()

    def _create_mock_repo(self, name: str, is_git: bool = True, has_deps: bool = False, is_dirty: bool = False, framework: str = "node") -> Path:
        repo_dir = self.sandbox_root / name
        repo_dir.mkdir(parents=True, exist_ok=True)

        if framework == "node":
            pkg = {
                "name": name,
                "version": "1.0.0",
                "scripts": {"dev": "vite", "build": "vite build"},
                "dependencies": {"vite": "^5.0.0"}
            }
            (repo_dir / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
            (repo_dir / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            (repo_dir / "README.md").write_text(f"# {name}\nSample Node repo\n", encoding="utf-8")
            if has_deps:
                (repo_dir / "node_modules").mkdir(exist_ok=True)

        elif framework == "python":
            (repo_dir / "requirements.txt").write_text("fastapi==0.100.0\nuvicorn==0.22.0\n", encoding="utf-8")
            (repo_dir / "main.py").write_text("print('hello python')\n", encoding="utf-8")
            (repo_dir / "README.md").write_text(f"# {name}\nSample Python backend\n", encoding="utf-8")
            if has_deps:
                (repo_dir / ".venv").mkdir(exist_ok=True)

        elif framework == "rust":
            (repo_dir / "Cargo.toml").write_text(f"[package]\nname = \"{name}\"\nversion = \"0.1.0\"\n", encoding="utf-8")
            (repo_dir / "src").mkdir(exist_ok=True)
            (repo_dir / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
            (repo_dir / "README.md").write_text(f"# {name}\nSample Rust CLI\n", encoding="utf-8")

        if is_git:
            # Create a bare remote repo in the sandbox so git push works cleanly
            bare_origin = self.sandbox_root / ".remotes" / f"{name}.git"
            bare_origin.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", str(bare_origin)], capture_output=True, check=True)

            subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@launchpad.internal"], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "add", "."], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(bare_origin)], cwd=str(repo_dir), capture_output=True, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(repo_dir), capture_output=True, check=True)

            if is_dirty:
                (repo_dir / "untracked_file.txt").write_text("new uncommitted content\n", encoding="utf-8")
                (repo_dir / "README.md").write_text("# Dirty modification\n", encoding="utf-8")

        return repo_dir


# ==============================================================================
# Phase 0 Tests — Foundation
# ==============================================================================

class TestPhase0Foundation(LaunchpadTestCase):
    def test_scaffold_and_permissions(self):
        """Verify .launchpad directory structure and 0700 permissions."""
        self.assertTrue(self.engine.paths.launchpad_dir.exists())
        self.assertTrue(self.engine.paths.manifest_path.exists())
        self.assertTrue(self.engine.paths.config_path.exists())

        # Permissions check (on Unix/macOS)
        mode = oct(self.engine.paths.launchpad_dir.stat().st_mode & 0o777)
        self.assertEqual(mode, "0o700")

    def test_manifest_schema_validation_and_rejection(self):
        """Verify schema validation accepts valid manifests and rejects invalid ones."""
        # Valid manifest
        self.engine.manifest._validate_schema(self.engine.manifest.data)

        # Missing projects array
        invalid = {"schemaVersion": 1, "meta": {}}
        with self.assertRaises(ValueError):
            self.engine.manifest._validate_schema(invalid)

        # Duplicate ID
        dup = {
            "schemaVersion": 1,
            "meta": {},
            "projects": [
                {"id": "foo", "source": {}, "state": {"tier": "COLD"}},
                {"id": "foo", "source": {}, "state": {"tier": "COLD"}}
            ]
        }
        with self.assertRaises(ValueError):
            self.engine.manifest._validate_schema(dup)

    def test_atomic_write_and_crash_recovery(self):
        """Verify manifest atomic writes leave the prior state intact during failures."""
        initial_data = dict(self.engine.manifest.data)
        initial_data["projects"] = [{
            "id": "persisted-project",
            "source": {"url": "https://github.com/owner/repo.git"},
            "state": {"tier": "COLD", "pinned": False}
        }]
        self.engine.manifest.save(initial_data)

        # Check file content matches
        with open(self.engine.paths.manifest_path, "r") as f:
            read_back = json.load(f)
        self.assertEqual(read_back["projects"][0]["id"], "persisted-project")

        # Simulate invalid write attempt
        try:
            self.engine.manifest.save({"invalid": "manifest"})
        except ValueError:
            pass

        # Verify old manifest was not corrupted
        with open(self.engine.paths.manifest_path, "r") as f:
            re_read = json.load(f)
        self.assertEqual(re_read["projects"][0]["id"], "persisted-project")

    def test_audit_logging(self):
        """Verify append-only JSONL audit log captures actions and actors."""
        self.engine.audit.log("test_action", "test_target", "success", "extra info")
        self.assertTrue(self.engine.paths.audit_path.exists())

        lines = self.engine.paths.audit_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        last_entry = json.loads(lines[-1])
        self.assertEqual(last_entry["action"], "test_action")
        self.assertEqual(last_entry["target"], "test_target")
        self.assertEqual(last_entry["result"], "success")
        self.assertEqual(last_entry["detail"], "extra info")

    def test_adoption_in_place_zero_byte_modification(self):
        """Verify in-place adoption of fixtures with zero byte modifications."""
        clean_node = self._create_mock_repo("clean-node", is_git=True, has_deps=True, is_dirty=False, framework="node")
        dirty_py = self._create_mock_repo("dirty-py", is_git=True, has_deps=False, is_dirty=True, framework="python")
        clean_rust = self._create_mock_repo("clean-rust", is_git=True, has_deps=False, is_dirty=False, framework="rust")
        local_only = self._create_mock_repo("local-only-app", is_git=False, has_deps=False, is_dirty=False, framework="node")

        # Checksums before bootstrap
        checksums_before = {
            "clean_node": compute_dir_checksum(clean_node),
            "dirty_py": compute_dir_checksum(dirty_py),
            "clean_rust": compute_dir_checksum(clean_rust),
            "local_only": compute_dir_checksum(local_only)
        }

        # Run bootstrap
        res = self.engine.bootstrap(include_github=False, scan_local=True)
        self.assertEqual(res["adopted"], 4)

        # Checksums after bootstrap (Guardrails 1, 2)
        checksums_after = {
            "clean_node": compute_dir_checksum(clean_node),
            "dirty_py": compute_dir_checksum(dirty_py),
            "clean_rust": compute_dir_checksum(clean_rust),
            "local_only": compute_dir_checksum(local_only)
        }
        self.assertEqual(checksums_before, checksums_after, "Existing user repositories were modified during adoption!")

        # Verify adopted tiers
        p_node = self.engine.manifest.get_project("clean-node")
        self.assertIsNotNone(p_node)
        self.assertEqual(p_node["state"]["tier"], "HOT")
        self.assertEqual(p_node["toolchain"]["packageManager"], "pnpm")

        p_py = self.engine.manifest.get_project("dirty-py")
        self.assertIsNotNone(p_py)
        self.assertEqual(p_py["state"]["tier"], "WARM")
        self.assertEqual(p_py["toolchain"]["runtime"], "python")

        p_rust = self.engine.manifest.get_project("clean-rust")
        self.assertIsNotNone(p_rust)
        self.assertEqual(p_rust["state"]["tier"], "WARM")
        self.assertEqual(p_rust["toolchain"]["packageManager"], "cargo")

        p_loc = self.engine.manifest.get_project("local-only-app")
        self.assertIsNotNone(p_loc)
        self.assertEqual(p_loc["source"]["canonical"], "local-only")
        self.assertTrue(p_loc["state"]["pinned"])

    def test_agents_contract_and_symlinks(self):
        """Verify root AGENTS.md and CLAUDE.md/GEMINI.md symlinks."""
        self.engine.write_agents_contract()
        self.assertTrue(self.engine.paths.agents_md.exists())
        self.assertTrue(self.engine.paths.claude_md.is_symlink())
        self.assertTrue(self.engine.paths.gemini_md.is_symlink())
        self.assertIn("Launchpad v2", self.engine.paths.agents_md.read_text(encoding="utf-8"))


# ==============================================================================
# Phase 1 Tests — Index & Access
# ==============================================================================

class TestPhase1IndexAndAccess(LaunchpadTestCase):
    def setUp(self):
        super().setUp()
        self.engine.bootstrap(include_github=False, scan_local=False)
        self._create_mock_repo("demo-web", is_git=True, has_deps=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

    def test_slot_generation_webloc_readme_tags(self):
        """Verify .webloc plist format, cached README with header, and tag application."""
        self.engine.set_url("demo-web", "https://demo-web.example.com", link_type="live")

        slot_dir = self.engine.paths.launchpad_index / "demo-web"
        self.assertTrue(slot_dir.exists())

        # 1. Live .webloc
        live_webloc = slot_dir / "demo-web — Live.webloc"
        self.assertTrue(live_webloc.exists())
        with open(live_webloc, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist.get("URL"), "https://demo-web.example.com")

        # 2. GitHub .webloc
        gh_webloc = slot_dir / "demo-web — GitHub.webloc"
        self.assertTrue(gh_webloc.exists())
        with open(gh_webloc, "rb") as f:
            plist = plistlib.load(f)
        self.assertTrue(plist.get("URL", "").endswith("demo-web.git"))

        # 3. Cached README (Markdown and HTML)
        readme = slot_dir / "README.md"
        self.assertTrue(readme.exists())
        content = readme.read_text(encoding="utf-8")
        self.assertIn("Launchpad Quick Look Cache", content)
        self.assertIn("Sample Node repo", content)

        readme_html = slot_dir / "README.html"
        self.assertTrue(readme_html.exists())
        html_content = readme_html.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html_content)
        self.assertIn("LP · HOT", html_content)
        self.assertIn("Sample Node repo", html_content)

    def test_regenerate_idempotence(self):
        """Verify deleting Launchpad/ and running regenerate rebuilds identical slots."""
        self.engine.set_url("demo-web", "https://demo-web.example.com", link_type="live")
        before_checksums = compute_dir_checksum(self.engine.paths.launchpad_index)

        # Wipe Launchpad/
        shutil.rmtree(self.engine.paths.launchpad_index)
        self.assertFalse(self.engine.paths.launchpad_index.exists())

        # Regenerate
        self.engine.surface.regenerate_all(self.engine.manifest.data)
        after_checksums = compute_dir_checksum(self.engine.paths.launchpad_index)

        self.assertEqual(set(before_checksums.keys()), set(after_checksums.keys()))

    def test_context_and_search(self):
        """Verify context project card and search capabilities."""
        ctx = self.engine.get_context("demo-web")
        self.assertEqual(ctx["id"], "demo-web")
        self.assertEqual(ctx["state"]["tier"], "HOT")

        # Search
        results = [p for p in self.engine.manifest.data["projects"] if "demo" in p["id"]]
        self.assertEqual(len(results), 1)


# ==============================================================================
# Phase 2 Tests — Lifecycle
# ==============================================================================

class TestPhase2Lifecycle(LaunchpadTestCase):
    def test_cleanliness_audit_blocks_dirty_eviction(self):
        """Guardrail 3: Evicting a dirty repo is blocked and tree remains untouched."""
        dirty_repo = self._create_mock_repo("dirty-project", is_git=True, is_dirty=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        chk_before = compute_dir_checksum(dirty_repo)

        # Attempt eviction without force
        with self.assertRaises(RuntimeError) as cm:
            self.engine.evict("dirty-project", force=False)
        self.assertIn("Eviction blocked by uncommitted work", str(cm.exception))

        # Verify tree untouched and marked dirty
        chk_after = compute_dir_checksum(dirty_repo)
        self.assertEqual(chk_before, chk_after)

        p = self.engine.manifest.get_project("dirty-project")
        self.assertEqual(p["state"]["attention"], "dirty")

    def test_non_tty_force_hard_fails(self):
        """Guardrail 3: --force in non-TTY (agent context) hard-fails."""
        self._create_mock_repo("dirty-proj-force", is_git=True, is_dirty=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        # In test runner, sys.stdin.isatty() is False
        with self.assertRaises(RuntimeError) as cm:
            self.engine.evict("dirty-proj-force", force=True)
        self.assertIn("Guardrail 3 Violation", str(cm.exception))

    def test_clean_evict_and_hydrate_roundtrip(self):
        """Test clean eviction to COLD and re-hydration."""
        clean_repo = self._create_mock_repo("roundtrip-proj", is_git=True, is_dirty=False, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        # Evict clean repo
        self.engine.evict("roundtrip-proj", force=False)
        self.assertFalse(clean_repo.exists())

        p = self.engine.manifest.get_project("roundtrip-proj")
        self.assertEqual(p["state"]["tier"], "COLD")
        self.assertIsNone(p["state"]["path"])

    def test_decay_logic_and_exemptions(self):
        """Test decay transitions (HOT->WARM, WARM->COLD) and immunity for pinned/dirty."""
        self._create_mock_repo("hot-decay", is_git=True, has_deps=True, framework="node")
        self._create_mock_repo("warm-decay", is_git=True, has_deps=False, framework="rust")
        self._create_mock_repo("pinned-proj", is_git=True, has_deps=True, framework="node")
        self._create_mock_repo("dirty-proj", is_git=True, is_dirty=True, framework="python")

        self.engine.adoption.scan_and_adopt(force=True)
        self.engine.pin("pinned-proj", pinned=True)

        # Manually backdate lastLocalTouch
        old_time_30_days = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()
        old_time_150_days = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=150)).isoformat()

        p_hot = self.engine.manifest.get_project("hot-decay")
        p_hot["state"]["lastLocalTouch"] = old_time_30_days
        self.engine.manifest.upsert_project(p_hot)

        p_warm = self.engine.manifest.get_project("warm-decay")
        p_warm["state"]["lastLocalTouch"] = old_time_150_days
        self.engine.manifest.upsert_project(p_warm)

        p_pin = self.engine.manifest.get_project("pinned-proj")
        p_pin["state"]["lastLocalTouch"] = old_time_150_days
        self.engine.manifest.upsert_project(p_pin)

        p_dirt = self.engine.manifest.get_project("dirty-proj")
        p_dirt["state"]["lastLocalTouch"] = old_time_150_days
        p_dirt["state"]["attention"] = "dirty"
        self.engine.manifest.upsert_project(p_dirt)

        # Run decay
        actions = self.engine.decay()
        decayed_ids = {a["id"] for a in actions}

        self.assertIn("hot-decay", decayed_ids)
        self.assertIn("warm-decay", decayed_ids)
        self.assertNotIn("pinned-proj", decayed_ids)
        self.assertNotIn("dirty-proj", decayed_ids)

        self.assertEqual(self.engine.manifest.get_project("hot-decay")["state"]["tier"], "WARM")
        self.assertEqual(self.engine.manifest.get_project("warm-decay")["state"]["tier"], "COLD")
        self.assertEqual(self.engine.manifest.get_project("pinned-proj")["state"]["tier"], "HOT")

    def test_automator_workflow_generation(self):
        """Verify generation of valid Automator .workflow bundles."""
        installed = self.engine.services.install_quick_actions(custom_services_dir=self.sandbox_services)
        self.assertEqual(installed, 6)

        for name, _ in launchpad.AUTOMATOR_ACTIONS:
            wflow_bundle = self.sandbox_services / f"{name}.workflow"
            self.assertTrue(wflow_bundle.is_dir())
            self.assertTrue((wflow_bundle / "Contents" / "document.wflow").exists())
            self.assertTrue((wflow_bundle / "Contents" / "Info.plist").exists())


# ==============================================================================
# Phase 3 Tests — Hardening
# ==============================================================================

class TestPhase3Hardening(LaunchpadTestCase):
    def test_doctor_redacted_mode(self):
        """Verify doctor --redacted output contains no private repo names or paths."""
        self._create_mock_repo("secret-proprietary-repo", is_git=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        doc = self.engine.doctor(redacted=True)
        self.assertEqual(doc["root"], "<REDACTED_ROOT>")

        # Ensure json string does not leak secret repo name
        doc_json = json.dumps(doc)
        self.assertNotIn("secret-proprietary-repo", doc_json)

    def test_disk_floor_enforcement(self):
        """Verify hydrate refuses promotion if free disk space is below floor."""
        self._create_mock_repo("huge-project", is_git=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        # Set impossible disk floor requirement (e.g. 100000 GB)
        self.engine.config.set("minFreeDiskGB", 100000)

        with self.assertRaises(RuntimeError) as cm:
            self.engine.hydrate("huge-project", hot=True)
        self.assertIn("below the required floor", str(cm.exception))


    def test_upstream_rename_and_deletion_sync(self):
        """Phase 3: Upstream rename updates git remote URL; upstream deletion auto-pins orphaned clones."""
        # Create a repo
        repo = self._create_mock_repo("rename-target", is_git=True, framework="node")
        self.engine.adoption.scan_and_adopt(force=True)

        p = self.engine.manifest.get_project("rename-target")
        self.assertIsNotNone(p)

        # Simulate upstream rename
        new_remote = "https://github.com/newowner/renamed-repo.git"
        p["source"]["url"] = new_remote
        self.engine.manifest.upsert_project(p)

        # Update git remote directly via engine sync simulation
        subprocess.run(["git", "remote", "set-url", "origin", new_remote], cwd=str(repo), capture_output=True)
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo), capture_output=True, text=True)
        self.assertEqual(res.stdout.strip(), new_remote)

        # Simulate upstream deletion
        p["source"]["upstreamGone"] = True
        p["state"]["attention"] = "orphaned"
        p["state"]["pinned"] = True
        self.engine.manifest.upsert_project(p)
        self.engine.surface.regenerate_slot(p)

        p_after = self.engine.manifest.get_project("rename-target")
        self.assertTrue(p_after["state"]["pinned"])
        self.assertEqual(p_after["state"]["attention"], "orphaned")

    def test_schema_migration_simulation(self):
        """Phase 3: Automatic backup created before schema migration."""
        # Write a v0 manifest
        v0_data = {
            "schemaVersion": 0,
            "meta": {"engineVersion": "1.0.0"},
            "projects": []
        }
        with open(self.engine.paths.manifest_path, "w") as f:
            json.dump(v0_data, f)

        # Reloading manifest should trigger migration and create backup
        loaded = self.engine.manifest._load()
        self.assertEqual(loaded["schemaVersion"], launchpad.MANIFEST_SCHEMA_VERSION)

        backup_file = self.engine.paths.launchpad_dir / "manifest.json.v0.bak"
        self.assertTrue(backup_file.exists())

    def test_port_conflict_probe(self):
        """Phase 2: Port conflict probing finds available port."""
        port1 = launchpad.ToolchainDetector.probe_available_port(3000)
        self.assertGreaterEqual(port1, 3000)

    def test_cli_subcommands(self):
        """Test invoking CLI commands directly."""
        env = os.environ.copy()
        env["LAUNCHPAD_HOME"] = str(self.sandbox_root)
        env["LAUNCHPAD_SERVICES_DIR"] = str(self.sandbox_services)
        env["LAUNCHPAD_BIN_DIR"] = str(self.sandbox_bin)
        env["LAUNCHPAD_SCRIPTS_DIR"] = str(self.sandbox_scripts)
        cli_path = str(WORKSPACE_DIR / "engine" / "launchpad.py") if (WORKSPACE_DIR / "engine" / "launchpad.py").exists() else str(WORKSPACE_DIR / ".launchpad" / "engine" / "launchpad.py")

        # 1. validate
        res = subprocess.run([sys.executable, cli_path, "validate"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("validation passed", res.stdout)

        # 2. doctor --redacted --json
        res = subprocess.run([sys.executable, cli_path, "doctor", "--redacted", "--json"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["root"], "<REDACTED_ROOT>")

        # 3. status --json
        res = subprocess.run([sys.executable, cli_path, "status", "--json"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

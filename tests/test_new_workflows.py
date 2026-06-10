#!/usr/bin/env python3
"""Tests for Candidate Discovery and New Contributor workflows."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import candidate_discovery
import new_contributor


class TestCandidateDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.old_state_dir = candidate_discovery.STATE_DIR
        candidate_discovery.STATE_DIR = Path(self.temp_dir)
        candidate_discovery.CANDIDATES_FILE = Path(self.temp_dir) / "candidates.json"
        candidate_discovery.ACTIONS_FILE = Path(self.temp_dir) / "actions.jsonl"
        candidate_discovery.PROGRESS_FILE = Path(self.temp_dir) / "progress.md"
        candidate_discovery.CONFIG_FILE = Path(self.temp_dir) / "config.json"
        
        # Create config
        config = {"user": {"login": "TestUser"}, "limits": {"max_open_per_repo": 30}}
        Path(self.temp_dir, "config.json").write_text(json.dumps(config))
    
    def tearDown(self):
        candidate_discovery.STATE_DIR = self.old_state_dir
    
    def test_load_candidates_empty(self):
        data = candidate_discovery.load_candidates()
        self.assertIn("candidates", data)
        self.assertEqual(data["candidates"], [])
    
    def test_load_candidates_existing(self):
        existing = {"_version": "0.1.0", "candidates": [{"type": "issue", "repo": "test/repo"}]}
        candidate_discovery.CANDIDATES_FILE.write_text(json.dumps(existing))
        data = candidate_discovery.load_candidates()
        self.assertEqual(len(data["candidates"]), 1)
    
    def test_score_repo_high_stars(self):
        repo_data = {"stargazersCount": 5000, "openIssuesCount": 100, "pushedAt": "2026-06-09T00:00:00Z"}
        score = candidate_discovery.score_repo(repo_data)
        self.assertGreater(score, 5.0)
    
    def test_score_repo_low_stars(self):
        repo_data = {"stargazersCount": 100, "openIssuesCount": 50, "pushedAt": "2026-06-01T00:00:00Z"}
        score = candidate_discovery.score_repo(repo_data)
        self.assertLess(score, 5.0)
        self.assertGreater(score, 0)
    
    def test_score_issue_with_preferred_labels(self):
        issue_data = {
            "labels": [{"name": "good first issue"}, {"name": "bug"}],
            "updatedAt": "2026-06-09T00:00:00Z",
            "createdAt": "2026-06-01T00:00:00Z",
        }
        score = candidate_discovery.score_issue(issue_data, 5.0)
        self.assertGreater(score, 7.0)
    
    def test_score_issue_without_preferred_labels(self):
        issue_data = {
            "labels": [{"name": "feature"}],
            "updatedAt": "2026-05-01T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
        }
        score = candidate_discovery.score_issue(issue_data, 3.0)
        self.assertLess(score, 5.0)
    
    def test_estimate_scope_tiny(self):
        issue_data = {"title": "Fix typo in README", "body": "There is a typo"}
        scope = candidate_discovery.estimate_scope(issue_data)
        self.assertEqual(scope, "tiny")
    
    def test_estimate_scope_small(self):
        issue_data = {"title": "Bug in parser", "body": "The parser crashes on input"}
        scope = candidate_discovery.estimate_scope(issue_data)
        self.assertEqual(scope, "small")
    
    def test_estimate_scope_medium(self):
        issue_data = {"title": "Add new feature", "body": "Implement support for X"}
        scope = candidate_discovery.estimate_scope(issue_data)
        self.assertEqual(scope, "medium")
    
    def test_has_reproduction(self):
        issue_data = {"body": "Here is the reproduction steps: 1. run 2. crash"}
        self.assertTrue(candidate_discovery.has_reproduction(issue_data))
    
    def test_has_tests_hint(self):
        issue_data = {"body": "We should add pytest coverage"}
        self.assertTrue(candidate_discovery.has_tests_hint(issue_data))
    
    def test_dedupe_key(self):
        key = candidate_discovery.dedupe_key("owner/repo", 123)
        self.assertEqual(key, "owner/repo#123")
    
    def test_find_existing_candidate(self):
        data = {"candidates": [{"dedupe_key": "test#1"}, {"dedupe_key": "test#2"}]}
        found = candidate_discovery.find_existing_candidate(data, "test#1")
        self.assertIsNotNone(found)
        self.assertEqual(found["dedupe_key"], "test#1")
    
    def test_find_existing_candidate_not_found(self):
        data = {"candidates": [{"dedupe_key": "test#1"}]}
        found = candidate_discovery.find_existing_candidate(data, "test#99")
        self.assertIsNone(found)


class TestNewContributor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.old_state_dir = new_contributor.STATE_DIR
        new_contributor.STATE_DIR = Path(self.temp_dir)
        new_contributor.CANDIDATES_FILE = Path(self.temp_dir) / "candidates.json"
        new_contributor.QUEUE_FILE = Path(self.temp_dir) / "queue.json"
        new_contributor.ACTIONS_FILE = Path(self.temp_dir) / "actions.jsonl"
        new_contributor.PROGRESS_FILE = Path(self.temp_dir) / "progress.md"
        new_contributor.CONFIG_FILE = Path(self.temp_dir) / "config.json"
        
        # Create config
        config = {"user": {"login": "TestUser"}, "limits": {"max_open_per_repo": 30}}
        Path(self.temp_dir, "config.json").write_text(json.dumps(config))
    
    def tearDown(self):
        new_contributor.STATE_DIR = self.old_state_dir
    
    def test_load_candidates(self):
        data = {"candidates": [{"type": "issue", "status": "new", "repo": "test/repo", "total_score": 8.0}]}
        new_contributor.CANDIDATES_FILE.write_text(json.dumps(data))
        loaded = new_contributor.load_candidates()
        self.assertEqual(len(loaded["candidates"]), 1)
    
    def test_select_candidates(self):
        data = {
            "candidates": [
                {"type": "issue", "status": "new", "repo": "a", "total_score": 10.0, "requires_domain_knowledge": False},
                {"type": "issue", "status": "new", "repo": "b", "total_score": 5.0, "requires_domain_knowledge": False},
                {"type": "repo", "status": "new", "repo": "c", "total_score": 8.0},
                {"type": "issue", "status": "contributed", "repo": "d", "total_score": 9.0},
            ]
        }
        selected = new_contributor.select_candidates(data, 2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["repo"], "a")
        self.assertEqual(selected[1]["repo"], "b")
    
    def test_select_candidates_filters_domain_knowledge(self):
        data = {
            "candidates": [
                {"type": "issue", "status": "new", "repo": "a", "total_score": 7.0, "requires_domain_knowledge": True},
                {"type": "issue", "status": "new", "repo": "b", "total_score": 8.0, "requires_domain_knowledge": False},
            ]
        }
        selected = new_contributor.select_candidates(data, 2)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["repo"], "b")
    
    def test_lint_comment_valid(self):
        ok, msg = new_contributor.lint_comment("This is a valid comment.")
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
    
    def test_lint_comment_empty(self):
        ok, msg = new_contributor.lint_comment("")
        self.assertFalse(ok)
        self.assertEqual(msg, "empty comment")
    
    def test_lint_comment_too_long(self):
        ok, msg = new_contributor.lint_comment("x" * 601)
        self.assertFalse(ok)
        self.assertEqual(msg, "comment exceeds 600 chars")
    
    def test_lint_comment_banned_phrase(self):
        ok, msg = new_contributor.lint_comment("This is furthermore a test.")
        self.assertFalse(ok)
        self.assertIn("furthermore", msg)
    
    def test_lint_comment_em_dash(self):
        ok, msg = new_contributor.lint_comment("This \u2014 is a test")
        self.assertFalse(ok)
        self.assertEqual(msg, "comment contains em/en dash")
    
    def test_lint_comment_bullet_list(self):
        ok, msg = new_contributor.lint_comment("- Item 1\n- Item 2")
        self.assertFalse(ok)
        self.assertEqual(msg, "comment contains bullet/list formatting")
    
    def test_lint_comment_close_word(self):
        ok, msg = new_contributor.lint_comment("I will close this soon.")
        self.assertFalse(ok)
        self.assertEqual(msg, "comment mentions closing, which is disabled by policy")
    
    def test_parse_patch_plan(self):
        output = json.dumps({
            "summary": "fixed bug",
            "commit_message": "fix: bug",
            "confidence": 8,
            "edits": [{"path": "test.py", "search": "old", "replace": "new"}],
            "test_commands": [["pytest", "test.py"]],
        })
        plan = new_contributor.parse_patch_plan(output)
        self.assertEqual(plan.summary, "fixed bug")
        self.assertEqual(plan.confidence, 8)
        self.assertEqual(len(plan.edits), 1)
        self.assertEqual(plan.edits[0].path, "test.py")
    
    def test_validate_patch_plan_low_confidence(self):
        plan = new_contributor.PatchPlan("test", "msg", [], [], 3)
        with self.assertRaises(RuntimeError) as ctx:
            new_contributor.validate_patch_plan(plan)
        self.assertIn("confidence", str(ctx.exception).lower())
    
    def test_validate_patch_plan_no_edits(self):
        plan = new_contributor.PatchPlan("test", "msg", [], [], 8)
        with self.assertRaises(RuntimeError) as ctx:
            new_contributor.validate_patch_plan(plan)
        self.assertIn("no edits", str(ctx.exception).lower())
    
    def test_validate_patch_plan_valid(self):
        plan = new_contributor.PatchPlan(
            "test", "msg",
            [new_contributor.PatchEdit("test.py", "old", "new")],
            [["pytest"]], 8
        )
        new_contributor.validate_patch_plan(plan)
    
    def test_apply_patch_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "test.py").write_text("old text")
            plan = new_contributor.PatchPlan(
                "test", "msg",
                [new_contributor.PatchEdit("test.py", "old text", "new text")],
                [], 8
            )
            touched = new_contributor.apply_patch_plan(repo_path, plan)
            self.assertEqual(touched, ["test.py"])
            self.assertEqual((repo_path / "test.py").read_text(), "new text")
    
    def test_apply_patch_plan_exact_match_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "test.py").write_text("old text\nold text")
            plan = new_contributor.PatchPlan(
                "test", "msg",
                [new_contributor.PatchEdit("test.py", "old text", "new text")],
                [], 8
            )
            with self.assertRaises(RuntimeError) as ctx:
                new_contributor.apply_patch_plan(repo_path, plan)
            self.assertIn("matched 2 times", str(ctx.exception))
    
    def test_apply_patch_plan_deletion_guardrail(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            lines = "\n".join(["line"] * 200)
            (repo_path / "test.py").write_text(lines)
            plan = new_contributor.PatchPlan(
                "test", "msg",
                [new_contributor.PatchEdit("test.py", lines, "short")],
                [], 8
            )
            with self.assertRaises(RuntimeError) as ctx:
                new_contributor.apply_patch_plan(repo_path, plan)
            self.assertIn("hard cap 150", str(ctx.exception))
    
    def test_default_test_commands_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "setup.py").write_text("")
            cmds = new_contributor.default_test_commands(repo_path)
            self.assertEqual(cmds, [["pytest", "-q"]])
    
    def test_default_test_commands_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "package.json").write_text("")
            cmds = new_contributor.default_test_commands(repo_path)
            self.assertEqual(cmds, [["npm", "test"]])
    
    def test_default_test_commands_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "go.mod").write_text("")
            cmds = new_contributor.default_test_commands(repo_path)
            self.assertEqual(cmds, [["go", "test", "./..."]])
    
    def test_default_test_commands_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            cmds = new_contributor.default_test_commands(repo_path)
            self.assertEqual(cmds, [])
    
    def test_update_candidate_status(self):
        data = {
            "candidates": [
                {"dedupe_key": "test#1", "status": "new"},
                {"dedupe_key": "test#2", "status": "new"},
            ]
        }
        new_contributor.update_candidate_status(data, "test#1", "contributed")
        self.assertEqual(data["candidates"][0]["status"], "contributed")
        self.assertEqual(data["candidates"][1]["status"], "new")
    
    def test_add_to_queue(self):
        new_contributor.QUEUE_FILE.write_text(json.dumps({"queue": ["existing#1"]}))
        new_contributor.add_to_queue("test/repo", 123)
        data = json.loads(new_contributor.QUEUE_FILE.read_text())
        self.assertEqual(data["queue"][0], "test/repo#123")
        self.assertEqual(data["queue"][1], "existing#1")
    
    def test_add_to_queue_dedupe(self):
        new_contributor.QUEUE_FILE.write_text(json.dumps({"queue": ["test/repo#123"]}))
        new_contributor.add_to_queue("test/repo", 123)
        data = json.loads(new_contributor.QUEUE_FILE.read_text())
        self.assertEqual(len(data["queue"]), 1)


if __name__ == "__main__":
    unittest.main()

import inspect
import unittest
from datetime import datetime, timezone
from unittest import mock

from agent import backlog_steward as steward


class BacklogStewardTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)

    def test_select_batch_skips_future_cooldown_and_attention(self):
        # In the new design, items in cooldown/needs_attention are NOT in queue
        state = {
            "queue": ["c/repo#3"],
            "cooldown": [
                {"key": "a/repo#1", "next_review_at": "2026-06-11T09:00:00Z"}
            ],
            "needs_attention": [{"key": "b/repo#2", "kind": "cla_blocked"}],
        }
        self.assertEqual(steward.select_batch(state, 2, self.now), ["c/repo#3"])

    def test_queue_cooldown_removes_from_queue(self):
        state = {"queue": ["a/repo#1", "b/repo#2"], "cooldown": []}
        steward.set_cooldown(state, "a/repo#1", "nudge", "2026-06-17T09:00:00Z")
        self.assertEqual(state["queue"], ["b/repo#2"])
        self.assertEqual(state["cooldown"][0]["key"], "a/repo#1")

    def test_close_is_blocked_by_executor(self):
        state = {"queue": ["a/repo#1"], "cooldown": [], "needs_attention": []}
        decision = steward.Decision("close", "model wanted close", 9, "closing")
        record = steward.execute_decision(
            "a/repo#1",
            "a/repo",
            1,
            decision,
            {"state": "OPEN"},
            self._activity(),
            "no checks",
            [],
            {"user": {"login": "Mr-Neutr0n"}},
            state,
            True,
        )
        self.assertEqual(record.type, "skip")
        self.assertIn("close is disabled", record.reason)

    def _activity(self):
        return steward.Activity(
            latest_author="Mr-Neutr0n",
            latest_author_association="CONTRIBUTOR",
            latest_body="bump",
            latest_created_at="2026-06-01T09:00:00Z",
            last_hari_comment_at="2026-06-01T09:00:00Z",
            last_maintainer_comment_at=None,
            should_nudge=True,
            should_reply=False,
            next_review_at="2026-06-08T09:00:00Z",
            reason="latest comment is Hari's and threshold passed",
        )

    def test_comment_lint_blocks_ai_style(self):
        ok, reason = steward.lint_comment("This looks good — furthermore, thanks")
        self.assertFalse(ok)
        self.assertTrue("dash" in reason or "banned" in reason)

    def test_comment_lint_blocks_close_offer(self):
        ok, reason = steward.lint_comment("Happy to close this if it is no longer useful.")
        self.assertFalse(ok)
        self.assertIn("closing", reason)

    def test_local_issue_lint_allows_close_word(self):
        ok, reason = steward.lint_comment("After resolving it, close this local issue.", allow_close_word=True)
        self.assertTrue(ok, reason)

    def test_nudge_rule_hari_latest_after_seven_days(self):
        pr = {"updatedAt": "2026-06-01T09:00:00Z"}
        comments = [
            {
                "user": {"login": "Mr-Neutr0n"},
                "body": "Friendly bump",
                "created_at": "2026-06-01T09:00:00Z",
                "author_association": "CONTRIBUTOR",
            }
        ]
        activity = steward.classify_activity(pr, comments, "Mr-Neutr0n", self.now)
        self.assertTrue(activity.should_nudge)
        self.assertFalse(activity.should_reply)

    def test_nudge_rule_blocks_before_seven_days(self):
        pr = {"updatedAt": "2026-06-05T09:00:00Z"}
        comments = [
            {
                "user": {"login": "Mr-Neutr0n"},
                "body": "Friendly bump",
                "created_at": "2026-06-05T09:00:00Z",
                "author_association": "CONTRIBUTOR",
            }
        ]
        activity = steward.classify_activity(pr, comments, "Mr-Neutr0n", self.now)
        self.assertFalse(activity.should_nudge)

    def test_maintainer_reply_wins_over_nudge(self):
        pr = {"updatedAt": "2026-06-09T09:00:00Z"}
        comments = [
            {
                "user": {"login": "Mr-Neutr0n"},
                "body": "Friendly bump",
                "created_at": "2026-06-01T09:00:00Z",
                "author_association": "CONTRIBUTOR",
            },
            {
                "user": {"login": "maintainer"},
                "body": "Can you add a test?",
                "created_at": "2026-06-09T09:00:00Z",
                "author_association": "MEMBER",
            },
        ]
        activity = steward.classify_activity(pr, comments, "Mr-Neutr0n", self.now)
        self.assertTrue(activity.should_reply)
        self.assertFalse(activity.should_nudge)

    def test_cla_blocker_detected_from_check_name(self):
        pr = {
            "statusCheckRollup": [
                {"name": "cla/google", "conclusion": "FAILURE", "status": "COMPLETED"}
            ],
            "labels": [],
        }
        self.assertTrue(steward.has_cla_blocker(pr))

    def test_failed_ci_becomes_fix_before_nudge(self):
        activity = self._activity()
        decision = steward.deterministic_decision("a/repo#1", {"state": "OPEN"}, activity, False, ["tests"])
        self.assertEqual(decision.action, "fix")

    def test_message_draft_retries_after_lint_failure(self):
        outputs = [
            '{"body":"Bad — furthermore","reason":"bad","confidence":8}',
            '{"body":"Checked this again. The diff is one file, +2/-1, and CI has no configured checks.","reason":"specific","confidence":8}',
        ]
        with mock.patch.object(steward, "run_opencode", side_effect=outputs):
            draft = steward.draft_message(
                "nudge",
                "a/repo#1",
                {"title": "Fix bug", "files": [{"path": "x.py"}], "additions": 2, "deletions": 1},
                self._activity(),
                "no checks",
            )
        self.assertEqual(draft.attempt, 2)
        self.assertIn("one file", draft.body)

    def test_public_text_fallback_phrases_are_not_in_source(self):
        source = inspect.getsource(steward)
        self.assertNotIn("Quick follow-up on this one", source)
        self.assertNotIn("Happy to adjust if you want a different direction", source)
        self.assertNotIn("fallback_nudge_decision", source)

    def test_patch_plan_validation_requires_edits_and_commit_message(self):
        with self.assertRaises(RuntimeError):
            steward.validate_patch_plan(
                steward.PatchPlan("summary", "", [], [], 8)
            )

    def test_apply_patch_plan_exactly_once(self):
        with self.subTest("missing match"):
            with self.assertRaises(RuntimeError):
                import tempfile
                from pathlib import Path

                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "x.py"
                    path.write_text("a = 1\n")
                    steward.apply_patch_plan(
                        Path(tmp),
                        steward.PatchPlan(
                            "summary",
                            "fix: test",
                            [steward.PatchEdit("x.py", "missing", "b = 2")],
                            [],
                            8,
                        ),
                    )

    def test_dry_run_fix_loop_does_not_push(self):
        record, draft = steward.run_fix_loop(
            "a/repo#1",
            "a/repo",
            1,
            {"headRepositoryOwner": {"login": "Mr-Neutr0n"}},
            self._activity(),
            "1/1 failing: tests",
            ["tests"],
            "Mr-Neutr0n",
            True,
        )
        self.assertEqual(record.type, "fix")
        self.assertIsNone(draft)

    def test_promote_expired_cooldown_moves_items_to_queue(self):
        state = {
            "queue": ["c/repo#3"],
            "cooldown": [
                {"key": "a/repo#1", "next_review_at": "2026-06-09T09:00:00Z"},  # expired
                {"key": "b/repo#2", "next_review_at": "2026-06-11T09:00:00Z"},  # not expired
            ],
        }
        promoted = steward.promote_expired_cooldown(state, self.now)
        self.assertEqual(promoted, 1)
        self.assertIn("a/repo#1", state["queue"])
        self.assertNotIn("b/repo#2", state["queue"])
        self.assertEqual(len(state["cooldown"]), 1)
        self.assertEqual(state["cooldown"][0]["key"], "b/repo#2")

    def test_cursor_advances_after_select(self):
        state = {"queue": ["a/repo#1", "b/repo#2", "c/repo#3"], "_cursor": 0}
        batch = steward.select_batch(state, 2, self.now)
        self.assertEqual(batch, ["a/repo#1", "b/repo#2"])
        self.assertEqual(state["_cursor"], 2)

    def test_cursor_wraps_around(self):
        state = {"queue": ["a/repo#1", "b/repo#2", "c/repo#3"], "_cursor": 2}
        batch = steward.select_batch(state, 2, self.now)
        self.assertEqual(batch, ["c/repo#3", "a/repo#1"])
        self.assertEqual(state["_cursor"], 1)

    def test_cooldown_skip_puts_item_in_cooldown(self):
        state = {"queue": ["a/repo#1"], "cooldown": [], "needs_attention": []}
        decision = steward.Decision("skip", "latest comment is Hari's but still in cooldown", 9)
        record = steward.execute_decision(
            "a/repo#1",
            "a/repo",
            1,
            decision,
            {"state": "OPEN"},
            self._activity(),
            "no checks",
            [],
            {"user": {"login": "Mr-Neutr0n"}},
            state,
            True,
        )
        self.assertEqual(record.state, "cooldown")
        self.assertNotIn("a/repo#1", state["queue"])
        self.assertEqual(state["cooldown"][0]["key"], "a/repo#1")

    def test_health_check_detects_stuck_queue(self):
        state = {"queue": ["a/repo#1"], "_last_selected": [["a/repo#1"]]}
        healthy = steward.health_check(state, ["a/repo#1"])
        self.assertTrue(healthy)  # only 2 runs, not 3
        healthy = steward.health_check(state, ["a/repo#1"])
        self.assertFalse(healthy)  # 3 consecutive same selections

    def test_non_cooldown_skip_removes_from_queue(self):
        state = {"queue": ["a/repo#1"], "cooldown": [], "needs_attention": []}
        decision = steward.Decision("skip", "PR state is MERGED", 10)
        record = steward.execute_decision(
            "a/repo#1",
            "a/repo",
            1,
            decision,
            {"state": "MERGED"},
            self._activity(),
            "no checks",
            [],
            {"user": {"login": "Mr-Neutr0n"}},
            state,
            True,
        )
        self.assertEqual(record.state, "skipped")
        self.assertNotIn("a/repo#1", state["queue"])
        self.assertEqual(len(state["cooldown"]), 0)


if __name__ == "__main__":
    unittest.main()

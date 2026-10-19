"""Tests for the switchable breakpoint layer.

Every test here must restore the module-global config and the environment, or
it poisons the ones that follow. The ``_fresh`` helper does that in setUp
rather than relying on tearDown ordering.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

from scorehalo import breakpoints as bpm


ENV_NAMES = (
    bpm.ENV_LABELS,
    bpm.ENV_ACTION,
    bpm.ENV_MATCH,
    bpm.ENV_SKIP,
    bpm.ENV_TIMES,
    bpm.ENV_WIDTH,
)


class _Fresh(unittest.TestCase):
    """Base: isolate the module's mutable state and environment."""

    def setUp(self):
        self._saved_env = {name: os.environ.get(name) for name in ENV_NAMES}
        for name in ENV_NAMES:
            os.environ.pop(name, None)
        bpm._CONFIG = bpm._Config()
        bpm._RUNTIME_LABELS = None
        bpm._RUNTIME_WILDCARD = None
        bpm._TRACE = []
        bpm._HITS = {}
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        bpm._CONFIG = bpm._Config()
        bpm._RUNTIME_LABELS = None
        bpm._RUNTIME_WILDCARD = None
        bpm._TRACE = []
        bpm._HITS = {}

    def run_bp(self, label, **context):
        """Call bp() capturing stderr so the test stays quiet."""
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            fired = bpm.bp(label, **context)
        return fired, buffer.getvalue()


class TestDisabledIsInert(_Fresh):
    def test_unset_env_fires_nothing(self):
        fired, out = self.run_bp("a", x=1)
        self.assertFalse(fired)
        self.assertEqual(out, "")
        self.assertEqual(bpm.trace(), [])

    def test_disabled_bp_records_no_hit_count(self):
        self.run_bp("a", x=1)
        self.assertEqual(bpm.hits(), {})

    def test_unarmed_label_among_armed_ones_does_not_fire(self):
        os.environ[bpm.ENV_LABELS] = "wanted"
        bpm.refresh()
        fired, _ = self.run_bp("other", x=1)
        self.assertFalse(fired)
        self.assertEqual(bpm.trace(), [])


class TestSelection(_Fresh):
    def test_named_label_fires(self):
        os.environ[bpm.ENV_LABELS] = "a,b"
        bpm.refresh()
        self.assertTrue(self.run_bp("a", x=1)[0])
        self.assertTrue(self.run_bp("b")[0])

    def test_wildcard_arms_everything(self):
        os.environ[bpm.ENV_LABELS] = "*"
        bpm.refresh()
        self.assertTrue(self.run_bp("anything", x=1)[0])

    def test_labels_are_trimmed_and_empty_pieces_ignored(self):
        os.environ[bpm.ENV_LABELS] = " a , , b "
        bpm.refresh()
        self.assertTrue(self.run_bp("a")[0])
        self.assertTrue(self.run_bp("b")[0])

    def test_wildcard_combined_with_names(self):
        os.environ[bpm.ENV_LABELS] = "named,*"
        bpm.refresh()
        self.assertTrue(self.run_bp("named")[0])
        self.assertTrue(self.run_bp("unlisted")[0])

    def test_match_substring_narrows_firing(self):
        os.environ[bpm.ENV_LABELS] = "*"
        os.environ[bpm.ENV_MATCH] = "part"
        bpm.refresh()
        self.assertTrue(self.run_bp("emit.part_parsed")[0])
        self.assertFalse(self.run_bp("emit.before_write")[0])

    def test_prefix_glob_arms_a_family(self):
        """The regression from first real use.

        ``SCOREHALO_BP='homr.*'`` was armed to watch every homr breakpoint and
        fired nothing, because the star was only honoured as a whole entry.
        The run then looked like a clean no-op rather than a mistake.
        """
        os.environ[bpm.ENV_LABELS] = "homr.*"
        bpm.refresh()
        self.assertTrue(self.run_bp("homr.nominator_in", x=1)[0])
        self.assertTrue(self.run_bp("homr.time_signature")[0])
        self.assertFalse(self.run_bp("emit.page")[0])

    def test_star_only_still_arms_everything(self):
        os.environ[bpm.ENV_LABELS] = "emit.page,*,homr.*"
        bpm.refresh()
        self.assertTrue(self.run_bp("anything_at_all")[0])

    def test_single_character_glob(self):
        os.environ[bpm.ENV_LABELS] = "homr.?ominator_in"
        bpm.refresh()
        self.assertTrue(self.run_bp("homr.nominator_in")[0])
        self.assertFalse(self.run_bp("homr.nominator_out")[0])

    def test_glob_and_exact_label_combine(self):
        os.environ[bpm.ENV_LABELS] = "exact,pre.*"
        bpm.refresh()
        self.assertTrue(self.run_bp("exact")[0])
        self.assertTrue(self.run_bp("pre.middle")[0])
        self.assertFalse(self.run_bp("prex.middle")[0])


class TestSkipAndTimes(_Fresh):
    def setUp(self):
        super().setUp()
        os.environ[bpm.ENV_LABELS] = "loop"

    def test_skip_suppresses_the_first_n_hits(self):
        os.environ[bpm.ENV_SKIP] = "2"
        bpm.refresh()
        results = [self.run_bp("loop")[0] for _ in range(4)]
        self.assertEqual(results, [False, False, True, True])

    def test_times_caps_total_hits(self):
        os.environ[bpm.ENV_TIMES] = "2"
        bpm.refresh()
        results = [self.run_bp("loop")[0] for _ in range(4)]
        self.assertEqual(results, [True, True, False, False])

    def test_hit_count_includes_suppressed_hits(self):
        os.environ[bpm.ENV_SKIP] = "1"
        os.environ[bpm.ENV_TIMES] = "1"
        bpm.refresh()
        self.run_bp("loop")
        self.run_bp("loop")
        self.run_bp("loop")
        self.assertEqual(bpm.hits()["loop"], 3)

    def test_negative_skip_falls_back_to_zero(self):
        os.environ[bpm.ENV_SKIP] = "-4"
        bpm.refresh()
        self.assertTrue(self.run_bp("loop")[0])

    def test_unparseable_times_falls_back_to_unlimited(self):
        os.environ[bpm.ENV_TIMES] = "many"
        bpm.refresh()
        results = [self.run_bp("loop")[0] for _ in range(3)]
        self.assertEqual(results, [True, True, True])

    def test_would_fire_does_not_consume_a_hit(self):
        os.environ[bpm.ENV_TIMES] = "1"
        bpm.refresh()
        self.assertTrue(bpm.would_fire("loop"))
        self.assertEqual(bpm.hits(), {})
        self.run_bp("loop")
        self.assertFalse(bpm.would_fire("loop"))
        self.assertEqual(bpm.hits(), {"loop": 1})


class TestActions(_Fresh):
    def setUp(self):
        super().setUp()
        os.environ[bpm.ENV_LABELS] = "a"

    def test_raise_action_raises_with_context_attached(self):
        os.environ[bpm.ENV_ACTION] = "raise"
        bpm.refresh()
        with self.assertRaises(bpm.BreakpointStop) as caught:
            self.run_bp("a", measure=7)
        self.assertEqual(caught.exception.label, "a")
        self.assertEqual(caught.exception.context["measure"], 7)
        self.assertEqual(caught.exception.hits, 1)

    def test_count_action_counts_but_records_nothing(self):
        os.environ[bpm.ENV_ACTION] = "count"
        bpm.refresh()
        for _ in range(3):
            self.assertTrue(self.run_bp("a")[0])
        self.assertEqual(bpm.hits()["a"], 3)
        self.assertEqual(bpm.trace(), [])

    def test_pdb_action_does_not_raise_when_debugger_fails(self):
        os.environ[bpm.ENV_ACTION] = "pdb"
        bpm.refresh()
        with mock.patch.object(bpm, "_debugger", side_effect=RuntimeError("boom")):
            fired, _ = self.run_bp("a", x=1)
        self.assertTrue(fired)

    def test_pdb_action_invokes_the_debugger_once(self):
        os.environ[bpm.ENV_ACTION] = "pdb"
        bpm.refresh()
        with mock.patch.object(bpm, "_debugger") as debugger:
            self.run_bp("a", x=1)
        debugger.assert_called_once()


class TestTraceAndReport(_Fresh):
    def test_trace_preserves_order_and_context(self):
        os.environ[bpm.ENV_LABELS] = "*"
        bpm.refresh()
        self.run_bp("first", n=1)
        self.run_bp("second", n=2)
        labels = [row["label"] for row in bpm.trace()]
        self.assertEqual(labels, ["first", "second"])
        self.assertEqual(bpm.trace()[1]["context"]["n"], 2)

    def test_trace_returns_a_copy(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        self.run_bp("a")
        snapshot = bpm.trace()
        snapshot.append("tampered")
        self.assertEqual(len(bpm.trace()), 1)

    def test_report_filters_by_label(self):
        os.environ[bpm.ENV_LABELS] = "*"
        bpm.refresh()
        self.run_bp("alpha", n=1)
        self.run_bp("beta", n=2)
        text = bpm.report(["beta"])
        self.assertIn("beta", text)
        self.assertNotIn("alpha", text)

    def test_report_on_empty_trace_says_so(self):
        self.assertIn("no breakpoint hits", bpm.report())

    def test_report_truncates_wide_values(self):
        os.environ[bpm.ENV_LABELS] = "a"
        os.environ[bpm.ENV_WIDTH] = "40"
        bpm.refresh()
        self.run_bp("a", big=list(range(200)))
        self.assertIn("more", bpm.report())

    def test_stderr_marks_oversized_sequences(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        _, out = self.run_bp("a", many=list(range(50)))
        self.assertIn("38 more", out)

    def test_context_without_keywords_is_labelled(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        _, out = self.run_bp("a")
        self.assertIn("no context", out)


class TestDump(_Fresh):
    def test_dump_writes_json_with_command_and_pid(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        self.run_bp("a", fraction="1/4")
        path = os.path.join("/tmp/opencode/bp-test", "trace.json")
        self.assertEqual(bpm.dump(path), path)
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["hits"], {"a": 1})
        self.assertEqual(payload["pid"], os.getpid())
        self.assertEqual(payload["command"], " ".join(sys.argv))
        self.assertEqual(payload["trace"][0]["context"]["fraction"], "1/4")
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    def test_dump_creates_missing_directories(self):
        target = os.path.join("/tmp/opencode/bp-test-nested", "deep", "t.json")
        bpm.dump(target)
        self.assertTrue(os.path.exists(target))
        os.remove(target)


class TestRuntimeSwitching(_Fresh):
    def test_enable_then_disable_within_one_process(self):
        self.assertFalse(self.run_bp("a")[0])
        bpm.enable("a")
        self.assertTrue(self.run_bp("a")[0])
        bpm.disable()
        self.assertFalse(self.run_bp("a")[0])

    def test_enable_with_no_arguments_arms_everything(self):
        bpm.enable()
        self.assertTrue(self.run_bp("whatever")[0])

    def test_enable_adds_to_environment_labels(self):
        os.environ[bpm.ENV_LABELS] = "from_env"
        bpm.refresh()
        bpm.enable("from_code")
        self.assertTrue(self.run_bp("from_env")[0])
        self.assertTrue(self.run_bp("from_code")[0])

    def test_disable_named_label_leaves_others_armed(self):
        bpm.enable("keep", "drop")
        bpm.disable("drop")
        self.assertTrue(self.run_bp("keep")[0])
        self.assertFalse(self.run_bp("drop")[0])

    def test_disable_star_disarms_everything(self):
        bpm.enable("a", "b")
        bpm.disable("*")
        self.assertFalse(self.run_bp("a")[0])
        self.assertFalse(self.run_bp("b")[0])

    def test_enabled_reflects_arming_without_firing(self):
        self.assertFalse(bpm.enabled("a"))
        bpm.enable("a")
        self.assertTrue(bpm.enabled("a"))
        self.assertEqual(bpm.hits(), {})

    def test_reset_clears_trace_and_counts_but_keeps_arming(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        self.run_bp("a")
        bpm.reset()
        self.assertEqual(bpm.trace(), [])
        self.assertEqual(bpm.hits(), {})
        self.assertTrue(self.run_bp("a")[0])

    def test_runtime_enable_survives_a_disarmed_environment(self):
        # The regression that motivated the hot-path rework: an armed config
        # used to never re-read the environment, and a disarmed one did.
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        bpm.enable("a")
        os.environ[bpm.ENV_LABELS] = "b"
        bpm.refresh()
        self.assertTrue(self.run_bp("a")[0])
        self.assertFalse(self.run_bp("b")[0])

    def test_env_change_after_arming_needs_explicit_refresh(self):
        os.environ[bpm.ENV_LABELS] = "a"
        bpm.refresh()
        self.assertTrue(self.run_bp("a")[0])
        os.environ[bpm.ENV_LABELS] = "b"
        # No refresh: the already-armed config keeps its own labels.
        self.assertTrue(self.run_bp("a")[0])
        bpm.refresh()
        self.assertFalse(self.run_bp("a")[0])
        self.assertTrue(self.run_bp("b")[0])


class TestHotPathCost(_Fresh):
    def test_disabled_call_does_not_touch_the_trace_lock(self):
        with mock.patch.object(bpm, "_LOCK", mock.Mock(  # type: ignore[assignment]
                side_effect=AssertionError("hot path took the lock"))):
            for _ in range(1000):
                bpm.bp("a", payload=object())

    def test_disabled_call_builds_no_context_dict(self):
        # ``**context`` is collected by the caller, so assert instead that the
        # trace and hit map stay empty across many disabled calls.
        for _ in range(1000):
            bpm.bp("a", one=1, two=2)
        self.assertEqual(bpm.trace(), [])
        self.assertEqual(bpm.hits(), {})


class TestEnvironmentIsReadAtImport(unittest.TestCase):
    """The module must read SCOREHALO_BP on import, in a fresh process.

    Regression, and the worst bug this module has had: the hot path returns
    False without refreshing, so a config that had never been read looked
    identical to one with nothing armed. ``SCOREHALO_BP=... homr`` then ran to
    completion, printed nothing, and read as "no breakpoints fired" -- the
    instrumented run and the un-instrumented run were indistinguishable.

    Subprocesses, not in-process env fiddling, because that is the only way to
    exercise import-time reading.
    """

    def _run(self, code, **env):
        import subprocess

        environment = dict(os.environ)
        for name in ENV_NAMES:
            environment.pop(name, None)
        environment.update({key: value for key, value in env.items()})
        return subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, env=environment)

    def test_label_arms_without_any_explicit_refresh(self):
        result = self._run(
            "from scorehalo.breakpoints import bp; print(bp('x', n=1))",
            SCOREHALO_BP="x")
        self.assertIn("True", result.stdout)
        self.assertIn("[bp] x", result.stderr)

    def test_glob_arms_without_any_explicit_refresh(self):
        result = self._run(
            "from scorehalo.breakpoints import bp; print(bp('homr.nominator_in'))",
            SCOREHALO_BP="homr.*")
        self.assertIn("True", result.stdout)

    def test_unset_means_nothing_fires(self):
        result = self._run(
            "from scorehalo.breakpoints import bp; print(bp('x', n=1))")
        self.assertIn("False", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_raise_action_survives_the_process_boundary(self):
        result = self._run(
            "from scorehalo.breakpoints import bp\n"
            "try:\n"
            "    bp('boom', why='test')\n"
            "except Exception as exc:\n"
            "    print('caught:', exc.label, exc.context['why'])\n",
            SCOREHALO_BP="boom", SCOREHALO_BP_ACTION="raise")
        self.assertIn("caught: boom test", result.stdout)

    def test_times_limit_applies_in_a_fresh_process(self):
        result = self._run(
            "from scorehalo.breakpoints import bp\n"
            "print([bp('loop') for _ in range(4)])\n",
            SCOREHALO_BP="loop", SCOREHALO_BP_TIMES="2",
            SCOREHALO_BP_ACTION="count")
        self.assertIn("[True, True, False, False]", result.stdout)


class TestMalformedInput(_Fresh):
    def test_bad_action_string_does_not_crash(self):
        os.environ[bpm.ENV_LABELS] = "a"
        os.environ[bpm.ENV_ACTION] = "nonsense"
        bpm.refresh()
        fired, _ = self.run_bp("a", x=1)
        self.assertTrue(fired)

    def test_empty_labels_env_arms_nothing(self):
        os.environ[bpm.ENV_LABELS] = "  , , "
        bpm.refresh()
        self.assertFalse(self.run_bp("a")[0])

    def test_unterminated_regex_free_match_is_literal(self):
        # SCOREHALO_BP_MATCH is a substring test, not a regex: a stray "(" must
        # not turn a filter into a crash or a surprising match.
        os.environ[bpm.ENV_LABELS] = "*"
        os.environ[bpm.ENV_MATCH] = "("
        bpm.refresh()
        self.assertFalse(self.run_bp("plain_label")[0])
        self.assertTrue(self.run_bp("has(paren)")[0])

    def test_non_string_width_falls_back(self):
        os.environ[bpm.ENV_LABELS] = "a"
        os.environ[bpm.ENV_WIDTH] = "wide"
        bpm.refresh()
        self.assertTrue(self.run_bp("a")[0])


if __name__ == "__main__":
    unittest.main()
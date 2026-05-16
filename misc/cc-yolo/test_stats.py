#!/usr/bin/env python3
"""Unit tests for stats.py — native session file analyzer."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from stats import (
    Record,
    Turn,
    SessionStats,
    DayStats,
    DAY_MS,
    parse_ts_ms,
    parse_session_file,
    discover_session_files,
    derive_turns,
    detect_live_sessions,
    clip_turns,
    compute_session_stats,
    compute_day_stats,
    format_duration,
    format_report,
    _make_legend,
    _render_heatmap,
    _HEAT_CHARS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_user(ts_ms, is_tool_result=False, is_meta=False):
    return Record(kind="user", ts_ms=ts_ms,
                  is_tool_result=is_tool_result, is_meta=is_meta)


def _make_assistant(ts_ms, tool_names=None, model=None):
    return Record(kind="assistant", ts_ms=ts_ms,
                  tool_names=tool_names or [], model=model)


def _make_turn_duration(ts_ms, duration_ms):
    return Record(kind="turn_duration", ts_ms=ts_ms, duration_ms=duration_ms)


def _make_stop_hook(ts_ms):
    return Record(kind="stop_hook", ts_ms=ts_ms)


def _write_session_jsonl(tmpdir, project_name, session_id, records):
    """Write a fake session JSONL file with the given records."""
    project_dir = os.path.join(tmpdir, project_name)
    if not os.path.isdir(project_dir):
        os.makedirs(project_dir)
    path = os.path.join(project_dir, session_id + ".jsonl")
    with open(path, "w") as f:
        for rec in records:
            line = _record_to_jsonl(rec)
            f.write(json.dumps(line) + "\n")
    return path


def _record_to_jsonl(rec):
    """Convert a Record to a JSONL dict as it would appear in a session file."""
    ts_str = "2026-04-23T10:00:00.{:03d}Z".format(rec.ts_ms % 1000)
    if rec.kind == "user":
        content = [{"type": "tool_result"}] if rec.is_tool_result else "user prompt"
        return {
            "type": "user",
            "timestamp": ts_str,
            "isMeta": rec.is_meta,
            "message": {"content": content},
        }
    elif rec.kind == "assistant":
        content = []
        for name in (rec.tool_names or []):
            content.append({"type": "tool_use", "name": name, "id": "t1", "input": {}})
        if not content:
            content.append({"type": "text", "text": "response"})
        d = {
            "type": "assistant",
            "timestamp": ts_str,
            "message": {"content": content},
        }
        if rec.model:
            d["message"]["model"] = rec.model
        return d
    elif rec.kind == "turn_duration":
        return {
            "type": "system",
            "subtype": "turn_duration",
            "timestamp": ts_str,
            "durationMs": rec.duration_ms,
        }
    elif rec.kind == "stop_hook":
        return {
            "type": "system",
            "subtype": "stop_hook_summary",
            "timestamp": ts_str,
        }
    return {}


# ── Timestamp Parsing ────────────────────────────────────────────────────────


class TestParseTsMs(unittest.TestCase):
    def test_utc_with_z(self):
        ms = parse_ts_ms("2026-04-23T10:00:00.000Z")
        self.assertIsNotNone(ms)
        self.assertIsInstance(ms, int)

    def test_fractional_seconds(self):
        ms1 = parse_ts_ms("2026-04-23T10:00:00.000Z")
        ms2 = parse_ts_ms("2026-04-23T10:00:00.500Z")
        self.assertEqual(ms2 - ms1, 500)

    def test_no_fractional(self):
        ms = parse_ts_ms("2026-04-23T10:00:00Z")
        self.assertIsNotNone(ms)

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_ts_ms("not a timestamp"))


# ── Turn Derivation ──────────────────────────────────────────────────────────


class TestDeriveTurns(unittest.TestCase):
    def test_tier1_turn_duration(self):
        """Gold standard: use turn_duration.durationMs directly."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_stop_hook(5000),
            _make_turn_duration(5000, 4000),
        ]
        turns, has_open, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].start_ms, 1000)
        self.assertEqual(turns[0].end_ms, 5000)
        self.assertEqual(turns[0].duration_ms, 4000)
        self.assertFalse(has_open)

    def test_tier2_stop_hook(self):
        """Fallback: stop_hook timestamp - user prompt timestamp."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_stop_hook(5000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].duration_ms, 4000)

    def test_tier3_next_user_prompt(self):
        """Last resort: last activity timestamp as end."""
        records = [
            _make_user(1000),
            _make_assistant(4000),
            _make_user(8000),
            _make_assistant(9000),
            _make_stop_hook(10000),
            _make_turn_duration(10000, 2000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 2)
        # First turn: closed by next user prompt, uses last_activity_ts (assistant at 4000)
        self.assertEqual(turns[0].start_ms, 1000)
        self.assertEqual(turns[0].end_ms, 4000)
        self.assertEqual(turns[0].duration_ms, 3000)
        # Second turn: closed by turn_duration
        self.assertEqual(turns[1].duration_ms, 2000)

    def test_interrupted_turn_uses_tool_result_ts(self):
        """Interrupted turn uses tool_result timestamp, not last assistant."""
        records = [
            _make_user(1000),                             # turn starts
            _make_assistant(2000, tool_names=["Bash"]),   # Claude calls Bash
            _make_user(5000, is_tool_result=True),        # interrupted tool result at 5s
            _make_user(6000),                             # new user prompt (next turn)
            _make_assistant(7000),
            _make_turn_duration(8000, 2000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 2)
        # First turn: ended by tool_result at 5000, not assistant at 2000
        self.assertEqual(turns[0].start_ms, 1000)
        self.assertEqual(turns[0].end_ms, 5000)
        self.assertEqual(turns[0].duration_ms, 4000)

    def test_multiple_turns(self):
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_turn_duration(3000, 2000),
            _make_user(6000),
            _make_assistant(7000),
            _make_turn_duration(8000, 2000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].duration_ms, 2000)
        self.assertEqual(turns[1].duration_ms, 2000)

    def test_tool_result_not_new_turn(self):
        """Tool results (content=list) should not start a new turn."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["Bash"]),
            _make_user(3000, is_tool_result=True),
            _make_assistant(4000),
            _make_turn_duration(5000, 4000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].duration_ms, 4000)

    def test_meta_user_not_new_turn(self):
        """Meta user messages should not start a new turn."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_user(2500, is_meta=True),
            _make_assistant(3000),
            _make_turn_duration(4000, 3000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].duration_ms, 3000)

    def test_open_turn_running(self):
        """Open turn with no wait = has_open."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
        ]
        turns, has_open, *_ = derive_turns(records)
        self.assertEqual(len(turns), 0)
        self.assertTrue(has_open)

    def test_open_turn_waiting_on_ask(self):
        """Open turn waiting for AskUserQuestion still has_open."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
        ]
        turns, has_open, *_ = derive_turns(records)
        self.assertTrue(has_open)

    def test_no_records_no_turns(self):
        turns, has_open, *_ = derive_turns([])
        self.assertEqual(len(turns), 0)
        self.assertFalse(has_open)

    def test_only_assistant_records(self):
        records = [_make_assistant(1000)]
        turns, has_open, *_ = derive_turns(records)
        self.assertEqual(len(turns), 0)
        self.assertFalse(has_open)


# ── Wait Time Detection ──────────────────────────────────────────────────────


class TestWaitTimeDetection(unittest.TestCase):
    def test_ask_user_question_wait(self):
        """AskUserQuestion gap is subtracted from work time."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
            _make_user(5000, is_tool_result=True),  # user answered after 3s
            _make_assistant(6000),
            _make_turn_duration(7000, 6000),  # wall clock 6s
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].wait_ms, 3000)
        self.assertEqual(turns[0].duration_ms, 3000)  # 6000 - 3000

    def test_exit_plan_mode_wait(self):
        """ExitPlanMode gap is subtracted from work time."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["ExitPlanMode"]),
            _make_user(12000, is_tool_result=True),  # user took 10s
            _make_assistant(13000),
            _make_turn_duration(14000, 13000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].wait_ms, 10000)
        self.assertEqual(turns[0].duration_ms, 3000)  # 13000 - 10000

    def test_multiple_waits_in_one_turn(self):
        """Multiple AskUserQuestion calls accumulate wait time."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
            _make_user(5000, is_tool_result=True),  # 3s wait
            _make_assistant(6000, tool_names=["AskUserQuestion"]),
            _make_user(10000, is_tool_result=True),  # 4s wait
            _make_assistant(11000),
            _make_turn_duration(12000, 11000),  # 11s wall clock
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].wait_ms, 7000)  # 3000 + 4000
        self.assertEqual(turns[0].duration_ms, 4000)  # 11000 - 7000

    def test_non_wait_tool_not_subtracted(self):
        """Bash/Edit tool calls should NOT be subtracted."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["Bash"]),
            _make_user(5000, is_tool_result=True),
            _make_assistant(6000),
            _make_turn_duration(7000, 6000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].wait_ms, 0)
        self.assertEqual(turns[0].duration_ms, 6000)

    def test_wait_reset_between_turns(self):
        """Wait state resets between turns."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
            _make_user(5000, is_tool_result=True),
            _make_assistant(6000),
            _make_turn_duration(7000, 6000),
            _make_user(10000),
            _make_assistant(11000),
            _make_turn_duration(12000, 2000),
        ]
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].wait_ms, 3000)
        self.assertEqual(turns[1].wait_ms, 0)


# ── Clip Turns ───────────────────────────────────────────────────────────────


class TestClipTurns(unittest.TestCase):
    def test_turn_fully_inside_window(self):
        turns = [Turn(start_ms=2000, end_ms=5000, duration_ms=3000)]
        clipped = clip_turns(turns, 0, 10000)
        self.assertEqual(len(clipped), 1)
        self.assertEqual(clipped[0].duration_ms, 3000)

    def test_turn_fully_outside_window(self):
        turns = [Turn(start_ms=2000, end_ms=5000, duration_ms=3000)]
        clipped = clip_turns(turns, 6000, 10000)
        self.assertEqual(len(clipped), 0)

    def test_cross_midnight_day1(self):
        midnight = 86400000
        turns = [Turn(start_ms=82800000, end_ms=90000000, duration_ms=7200000)]
        clipped = clip_turns(turns, 0, midnight)
        self.assertEqual(len(clipped), 1)
        self.assertEqual(clipped[0].duration_ms, 3600000)

    def test_cross_midnight_day2(self):
        midnight = 86400000
        day2_end = 86400000 * 2
        turns = [Turn(start_ms=82800000, end_ms=90000000, duration_ms=7200000)]
        clipped = clip_turns(turns, midnight, day2_end)
        self.assertEqual(len(clipped), 1)
        self.assertEqual(clipped[0].duration_ms, 3600000)

    def test_clip_preserves_wait_ratio(self):
        turns = [Turn(start_ms=0, end_ms=4000, duration_ms=2000, wait_ms=2000)]
        clipped = clip_turns(turns, 0, 2000)
        self.assertEqual(clipped[0].duration_ms, 1000)
        self.assertEqual(clipped[0].wait_ms, 1000)


# ── Session Stats ────────────────────────────────────────────────────────────


class TestComputeSessionStats(unittest.TestCase):
    def test_full_session(self):
        records = [
            _make_user(0),
            _make_assistant(1000, model="opus"),
            _make_turn_duration(5000, 5000),
            _make_user(8000),
            _make_assistant(9000),
            _make_turn_duration(10000, 2000),
        ]
        stats = compute_session_stats("s1", records, {})
        self.assertEqual(stats.num_turns, 2)
        self.assertEqual(stats.total_work_ms, 7000)
        self.assertEqual(stats.total_between_ms, 3000)  # 8000 - 5000
        self.assertEqual(stats.model, "opus")

    def test_session_with_wait(self):
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
            _make_user(5000, is_tool_result=True),
            _make_assistant(6000),
            _make_turn_duration(7000, 6000),
        ]
        stats = compute_session_stats("s1", records, {})
        self.assertEqual(stats.total_work_ms, 3000)  # 6000 - 3000 wait
        self.assertEqual(stats.total_wait_ms, 3000)

    def test_empty_session(self):
        stats = compute_session_stats("s1", [], {})
        self.assertIsNone(stats)

    def test_live_session(self):
        records = [
            _make_user(1000),
            _make_assistant(2000),
        ]
        live = {"s1": {"pid": 12345, "cwd": "/tmp", "status": "busy"}}
        stats = compute_session_stats("s1", records, live, now_ms=10000)
        self.assertTrue(stats.is_running)
        self.assertTrue(stats.has_open_turn)

    def test_live_but_idle_session_not_running(self):
        """Live PID but idle status = not running."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
        ]
        live = {"s1": {"pid": 12345, "cwd": "/tmp", "status": "idle"}}
        stats = compute_session_stats("s1", records, live, now_ms=400000)
        self.assertFalse(stats.is_running)
        self.assertTrue(stats.has_open_turn)

    def test_live_session_partial_turn_counted(self):
        """Live session with open turn adds partial work up to now."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_turn_duration(5000, 4000),  # completed turn: 4s work
            _make_user(6000),                  # open turn started
            _make_assistant(7000),
        ]
        live = {"s1": {"pid": 12345, "cwd": "/tmp", "status": "busy"}}
        stats = compute_session_stats("s1", records, live, now_ms=16000)
        self.assertEqual(stats.num_turns, 2)
        # completed turn: 4000ms + partial turn: 16000 - 6000 = 10000ms
        self.assertEqual(stats.total_work_ms, 14000)

    def test_live_session_partial_turn_with_wait(self):
        """Partial turn subtracts pending wait time from AskUserQuestion."""
        records = [
            _make_user(1000),
            _make_assistant(2000, tool_names=["AskUserQuestion"]),
        ]
        live = {"s1": {"pid": 12345, "cwd": "/tmp", "status": "busy"}}
        stats = compute_session_stats("s1", records, live, now_ms=10000)
        self.assertEqual(stats.num_turns, 1)
        # partial turn: 10000 - 1000 = 9000 total, wait = 10000 - 2000 = 8000
        self.assertEqual(stats.total_wait_ms, 8000)
        self.assertEqual(stats.total_work_ms, 1000)

    def test_idle_session_no_partial_turn(self):
        """Idle live session should NOT add a partial turn."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
        ]
        live = {"s1": {"pid": 12345, "cwd": "/tmp", "status": "idle"}}
        stats = compute_session_stats("s1", records, live, now_ms=400000)
        self.assertEqual(stats.num_turns, 0)
        self.assertEqual(stats.total_work_ms, 0)

    def test_closed_session_no_partial_turn(self):
        """Non-live session with open turn should NOT add a partial turn."""
        records = [
            _make_user(1000),
            _make_assistant(2000),
        ]
        stats = compute_session_stats("s1", records, {}, now_ms=5000)
        self.assertEqual(stats.num_turns, 0)
        self.assertEqual(stats.total_work_ms, 0)

    def test_closed_session_not_running(self):
        records = [
            _make_user(1000),
            _make_assistant(2000),
            _make_turn_duration(3000, 2000),
        ]
        stats = compute_session_stats("s1", records, {})
        self.assertFalse(stats.is_running)
        self.assertFalse(stats.has_open_turn)


# ── Day Stats ────────────────────────────────────────────────────────────────


class TestComputeDayStats(unittest.TestCase):
    def test_aggregation(self):
        s1 = SessionStats(
            session_id="s1", model="opus", start_ms=0, end_ms=10000,
            session_duration_ms=10000, num_turns=3, total_work_ms=5000,
            total_wait_ms=1000, total_between_ms=2000,
            utilization=0.5, turns=[],
            avg_turn_ms=1667, median_turn_ms=1500, max_turn_ms=2000,
        )
        s2 = SessionStats(
            session_id="s2", model="opus", start_ms=20000, end_ms=30000,
            session_duration_ms=10000, num_turns=2, total_work_ms=3000,
            total_wait_ms=500, total_between_ms=1000,
            utilization=0.3, turns=[],
            avg_turn_ms=1500, median_turn_ms=1500, max_turn_ms=1500,
        )
        day = compute_day_stats([s1, s2])
        self.assertEqual(day.num_sessions, 2)
        self.assertEqual(day.total_turns, 5)
        self.assertEqual(day.total_work_ms, 8000)
        self.assertEqual(day.total_wait_ms, 1500)
        self.assertEqual(day.total_between_ms, 3000)
        self.assertEqual(day.total_day_ms, DAY_MS)
        self.assertAlmostEqual(day.utilization, 8000 / DAY_MS)


# ── Format Duration ──────────────────────────────────────────────────────────


class TestFormatDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(format_duration(5000), "5s")

    def test_minutes_seconds(self):
        self.assertEqual(format_duration(125000), "2m 05s")

    def test_hours_minutes(self):
        self.assertEqual(format_duration(3723000), "1h 02m 03s")

    def test_zero(self):
        self.assertEqual(format_duration(0), "0s")


# ── Format Report ────────────────────────────────────────────────────────────


class TestFormatReport(unittest.TestCase):
    def test_report_contains_key_sections(self):
        day = DayStats(
            date="2026-04-23",
            num_sessions=2, total_turns=10, total_work_ms=300000,
            total_wait_ms=10000, total_between_ms=50000,
            total_day_ms=DAY_MS, utilization=300000 / DAY_MS,
            sessions=[],
        )
        report = format_report(day)
        self.assertIn("CC Utilization Dashboard", report)
        self.assertIn("Daily Summary", report)
        self.assertIn("Score", report)
        self.assertIn("2026-04-23", report)


# ── Session File Discovery ───────────────────────────────────────────────────


class TestDiscoverSessionFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_finds_jsonl_files(self):
        proj = os.path.join(self.tmpdir, "-data-project")
        os.makedirs(proj)
        open(os.path.join(proj, "abc123.jsonl"), "w").close()
        results = discover_session_files(self.tmpdir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], "abc123")

    def test_ignores_subdirectories(self):
        proj = os.path.join(self.tmpdir, "-data-project")
        os.makedirs(os.path.join(proj, "subdir"))
        open(os.path.join(proj, "abc123.jsonl"), "w").close()
        open(os.path.join(proj, "subdir", "sub.jsonl"), "w").close()
        results = discover_session_files(self.tmpdir)
        self.assertEqual(len(results), 1)

    def test_mtime_filter(self):
        proj = os.path.join(self.tmpdir, "-data-project")
        os.makedirs(proj)
        path = os.path.join(proj, "old.jsonl")
        open(path, "w").close()
        # Set mtime to epoch 0
        os.utime(path, (0, 0))
        # clip_start_ms = now (should filter out the old file)
        import time
        results = discover_session_files(self.tmpdir, clip_start_ms=int(time.time() * 1000))
        self.assertEqual(len(results), 0)

    def test_nonexistent_dir(self):
        results = discover_session_files("/nonexistent/path")
        self.assertEqual(len(results), 0)


# ── JSONL Parser ─────────────────────────────────────────────────────────────


class TestParseSessionFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, records):
        path = os.path.join(self.tmpdir, "test.jsonl")
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return path

    def test_parses_user_record(self):
        path = self._write([{
            "type": "user",
            "timestamp": "2026-04-23T10:00:00.000Z",
            "message": {"content": "hello"},
        }])
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "user")
        self.assertFalse(records[0].is_tool_result)

    def test_parses_tool_result(self):
        path = self._write([{
            "type": "user",
            "timestamp": "2026-04-23T10:00:00.000Z",
            "message": {"content": [{"type": "tool_result"}]},
        }])
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].is_tool_result)

    def test_parses_assistant_with_tools(self):
        path = self._write([{
            "type": "assistant",
            "timestamp": "2026-04-23T10:00:00.000Z",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}],
                "model": "claude-opus-4-6",
            },
        }])
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "assistant")
        self.assertEqual(records[0].tool_names, ["Bash"])
        self.assertEqual(records[0].model, "claude-opus-4-6")

    def test_parses_turn_duration(self):
        path = self._write([{
            "type": "system",
            "subtype": "turn_duration",
            "timestamp": "2026-04-23T10:00:05.000Z",
            "durationMs": 4500,
        }])
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "turn_duration")
        self.assertEqual(records[0].duration_ms, 4500)

    def test_skips_records_without_timestamp(self):
        path = self._write([
            {"type": "permission-mode", "permissionMode": "default"},
            {"type": "user", "timestamp": "2026-04-23T10:00:00.000Z",
             "message": {"content": "hi"}},
        ])
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)

    def test_skips_malformed_lines(self):
        path = os.path.join(self.tmpdir, "bad.jsonl")
        with open(path, "w") as f:
            f.write("NOT JSON\n")
            f.write(json.dumps({
                "type": "user",
                "timestamp": "2026-04-23T10:00:00.000Z",
                "message": {"content": "hi"},
            }) + "\n")
        records = parse_session_file(path)
        self.assertEqual(len(records), 1)

    def test_empty_file(self):
        path = self._write([])
        records = parse_session_file(path)
        self.assertEqual(len(records), 0)

    def test_skips_forkedFrom_messages(self):
        """Messages with forkedFrom are inherited from parent session and should be skipped."""
        path = self._write([
            {"type": "user", "timestamp": "2026-04-23T09:00:00.000Z",
             "message": {"content": "original prompt"}, "forkedFrom": "abc123"},
            {"type": "assistant", "timestamp": "2026-04-23T09:00:05.000Z",
             "message": {"content": [{"type": "text", "text": "response"}], "model": "claude-opus-4-6"},
             "forkedFrom": "abc123"},
            {"type": "user", "timestamp": "2026-04-23T10:00:00.000Z",
             "message": {"content": "new prompt in branched session"}},
            {"type": "assistant", "timestamp": "2026-04-23T10:00:05.000Z",
             "message": {"content": [{"type": "text", "text": "new response"}], "model": "claude-opus-4-6"}},
        ])
        records = parse_session_file(path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].kind, "user")
        self.assertEqual(records[1].kind, "assistant")

    def test_forkedFrom_not_counted_as_turns(self):
        """Forked messages should not produce turns — only new activity counts."""
        path = self._write([
            {"type": "user", "timestamp": "2026-04-23T09:00:00.000Z",
             "message": {"content": "inherited"}, "forkedFrom": "abc123"},
            {"type": "assistant", "timestamp": "2026-04-23T09:00:05.000Z",
             "message": {"content": [{"type": "text", "text": "inherited reply"}], "model": "claude-opus-4-6"},
             "forkedFrom": "abc123"},
            {"type": "user", "timestamp": "2026-04-23T10:00:00.000Z",
             "message": {"content": "actual work"}},
            {"type": "assistant", "timestamp": "2026-04-23T10:00:10.000Z",
             "message": {"content": [{"type": "text", "text": "reply"}], "model": "claude-opus-4-6"}},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-04-23T10:00:10.000Z", "durationMs": 10000},
        ])
        records = parse_session_file(path)
        turns, *_ = derive_turns(records)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].start_ms, parse_ts_ms("2026-04-23T10:00:00.000Z"))


# ── Live Session Detection ───────────────────────────────────────────────────


class TestDetectLiveSessions(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_detects_own_pid(self):
        """Our own PID should be detected as alive."""
        pid = os.getpid()
        data = {"pid": pid, "sessionId": "test-session", "cwd": "/tmp"}
        with open(os.path.join(self.tmpdir, "{}.json".format(pid)), "w") as f:
            json.dump(data, f)
        live = detect_live_sessions(self.tmpdir)
        self.assertIn("test-session", live)

    def test_dead_pid_not_detected(self):
        data = {"pid": 999999999, "sessionId": "dead-session", "cwd": "/tmp"}
        with open(os.path.join(self.tmpdir, "999999999.json"), "w") as f:
            json.dump(data, f)
        live = detect_live_sessions(self.tmpdir)
        self.assertNotIn("dead-session", live)

    def test_nonexistent_dir(self):
        live = detect_live_sessions("/nonexistent/path")
        self.assertEqual(len(live), 0)

    def test_malformed_json_skipped(self):
        with open(os.path.join(self.tmpdir, "bad.json"), "w") as f:
            f.write("NOT JSON")
        live = detect_live_sessions(self.tmpdir)
        self.assertEqual(len(live), 0)


# ── CLI Integration ──────────────────────────────────────────────────────────


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.py")


def _make_session_jsonl(records):
    """Convert record dicts to JSONL lines."""
    lines = []
    for r in records:
        lines.append(json.dumps(r))
    return "\n".join(lines) + "\n"


SAMPLE_SESSION = [
    {"type": "user", "timestamp": "2026-04-23T10:00:00.000Z",
     "message": {"content": "hello"}},
    {"type": "assistant", "timestamp": "2026-04-23T10:00:02.000Z",
     "message": {"content": [{"type": "text", "text": "hi"}], "model": "opus"}},
    {"type": "system", "subtype": "stop_hook_summary",
     "timestamp": "2026-04-23T10:00:10.000Z"},
    {"type": "system", "subtype": "turn_duration",
     "timestamp": "2026-04-23T10:00:10.000Z", "durationMs": 10000},
]


def _setup_projects_dir(tmpdir, sessions_data=None):
    """Create a fake projects dir with session files."""
    projects_dir = os.path.join(tmpdir, "projects")
    project_dir = os.path.join(projects_dir, "-data-test")
    os.makedirs(project_dir)
    if sessions_data is None:
        sessions_data = {"sess-1": SAMPLE_SESSION}
    for sid, records in sessions_data.items():
        path = os.path.join(project_dir, sid + ".jsonl")
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    return projects_dir


def _run_cli(*extra_args, sessions_data=None):
    tmpdir = tempfile.mkdtemp()
    try:
        projects_dir = _setup_projects_dir(tmpdir, sessions_data)
        sessions_dir = os.path.join(tmpdir, "sessions")
        os.makedirs(sessions_dir)
        result = subprocess.run(
            ["python3", SCRIPT,
             "--projects-dir", projects_dir,
             "--sessions-dir", sessions_dir,
             "--date", "2026-04-23",
             *extra_args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        return result
    finally:
        shutil.rmtree(tmpdir)


# ── Heatmap ──────────────────────────────────────────────────────────────────


class TestHeatmap(unittest.TestCase):
    def test_legend_zero(self):
        self.assertEqual(_make_legend(0), "")

    def test_legend_shows_all_values(self):
        legend = _make_legend(3)
        self.assertIn("=1", legend)
        self.assertIn("=2", legend)
        self.assertIn("=3", legend)

    def test_legend_chars_match_render(self):
        """Legend characters must match what _render_heatmap produces."""
        max_v = 5
        legend = _make_legend(max_v)
        for v in range(1, max_v + 1):
            rendered = _render_heatmap([v, max_v])
            char = rendered[0]  # first char corresponds to v (ignoring ANSI)
            # Strip ANSI escape
            import re
            char = re.sub(r"\033\[[^m]*m", "", rendered)[0]
            self.assertIn("{}={}".format(char, v), legend)

    def test_render_zeros_are_spaces(self):
        import re
        raw = re.sub(r"\033\[[^m]*m", "", _render_heatmap([0, 0, 0]))
        self.assertEqual(raw, "   ")

    def test_render_max_is_full_block(self):
        import re
        raw = re.sub(r"\033\[[^m]*m", "", _render_heatmap([5, 5, 5]))
        self.assertEqual(raw, "███")

    def test_render_proportional(self):
        """Lower values get shorter blocks than higher values."""
        import re
        raw = re.sub(r"\033\[[^m]*m", "", _render_heatmap([1, 2, 3]))
        self.assertTrue(raw[0] < raw[1] < raw[2])

    def test_legend_groups_into_ranges(self):
        """Legend groups values into ranges by character."""
        legend = _make_legend(16)
        # 8 block chars → at most 8 entries
        parts = legend.split("  ")
        self.assertLessEqual(len(parts), 8)
        # First and last values covered
        self.assertTrue(parts[0].endswith("=1") or "=1-" in parts[0] or "-1" in parts[0])
        self.assertTrue(parts[-1].endswith("-16") or parts[-1].endswith("=16"))

    def test_legend_high_concurrency(self):
        """Legend with 100+ peak still limited to at most 8 entries."""
        legend = _make_legend(120)
        parts = legend.split("  ")
        self.assertLessEqual(len(parts), 8)
        # Covers full range
        self.assertIn("=1", parts[0])
        self.assertTrue(parts[-1].endswith("120"))

    def test_render_high_concurrency(self):
        """Heatmap renders correctly with peak concurrency of 100+."""
        import re
        values = list(range(0, 128))
        raw = re.sub(r"\033\[[^m]*m", "", _render_heatmap(values))
        self.assertEqual(len(raw), 128)
        self.assertEqual(raw[0], " ")
        self.assertEqual(raw[-1], "█")
        # Monotonically non-decreasing (excluding the space at 0)
        for i in range(2, len(raw)):
            self.assertGreaterEqual(raw[i], raw[i - 1])


class TestCLI(unittest.TestCase):
    def test_json_output(self):
        result = _run_cli("--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["sessions"], 1)
        self.assertEqual(data["turns"], 1)
        self.assertEqual(data["work_ms"], 10000)
        self.assertIn("active", data)

    def test_field_sessions(self):
        result = _run_cli("--field", "sessions")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "1")

    def test_field_turns(self):
        result = _run_cli("--field", "turns")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "1")

    def test_field_work_fmt(self):
        result = _run_cli("--field", "work_fmt")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "10s")

    def test_field_unknown_exits_nonzero(self):
        result = _run_cli("--field", "nonexistent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown field", result.stderr)

    def test_human_readable_report(self):
        result = _run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("CC Utilization Dashboard", result.stdout)
        self.assertIn("Daily Summary", result.stdout)
        self.assertIn("Score", result.stdout)

    def test_multiple_sessions(self):
        session2 = [
            {"type": "user", "timestamp": "2026-04-23T11:00:00.000Z",
             "message": {"content": "test"}},
            {"type": "assistant", "timestamp": "2026-04-23T11:00:02.000Z",
             "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-04-23T11:00:22.000Z", "durationMs": 20000},
        ]
        result = _run_cli("--json", sessions_data={
            "sess-1": SAMPLE_SESSION,
            "sess-2": session2,
        })
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["sessions"], 2)
        self.assertEqual(data["turns"], 2)
        self.assertEqual(data["work_ms"], 30000)

    def test_session_filter(self):
        session2 = [
            {"type": "user", "timestamp": "2026-04-23T11:00:00.000Z",
             "message": {"content": "test"}},
            {"type": "assistant", "timestamp": "2026-04-23T11:00:02.000Z",
             "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-04-23T11:00:22.000Z", "durationMs": 20000},
        ]
        result = _run_cli("--json", "--session", "sess-2", sessions_data={
            "sess-1": SAMPLE_SESSION,
            "sess-2": session2,
        })
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["sessions"], 1)
        self.assertEqual(data["work_ms"], 20000)

    def test_json_utilization_is_percentage(self):
        result = _run_cli("--json")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertGreater(data["utilization"], 0)
        self.assertLess(data["utilization"], 100)


if __name__ == "__main__":
    unittest.main()

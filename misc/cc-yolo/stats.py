#!/usr/bin/env python3
"""Claude Code utilization analyzer — reads native session JSONL files.

No plugin dependency. Derives turn timing directly from Claude Code's
session files at ~/.claude/projects/.
"""

import argparse
import calendar
import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone, timedelta

DEFAULT_TZ = datetime.now(timezone.utc).astimezone().tzinfo
DAY_MS = 24 * 60 * 60 * 1000
IDLE_THRESHOLD_MS = 300 * 1000  # used to detect api_error recovery vs session death

# Tools where the gap between tool_use and tool_result is user wait time
WAIT_TOOLS = frozenset(["AskUserQuestion", "ExitPlanMode"])


# ── Data Classes ─────────────────────────────────────────────────────────────


class Record(object):
    __slots__ = ("kind", "ts_ms", "tool_names", "is_tool_result", "is_meta",
                 "duration_ms", "model")

    def __init__(self, kind, ts_ms, tool_names=None, is_tool_result=False,
                 is_meta=False, duration_ms=None, model=None):
        self.kind = kind              # "user", "assistant", "turn_duration", "stop_hook"
        self.ts_ms = ts_ms
        self.tool_names = tool_names  # list of tool names in assistant message
        self.is_tool_result = is_tool_result
        self.is_meta = is_meta
        self.duration_ms = duration_ms
        self.model = model


class Turn(object):
    __slots__ = ("start_ms", "end_ms", "duration_ms", "wait_ms")

    def __init__(self, start_ms, end_ms, duration_ms, wait_ms=0):
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.duration_ms = duration_ms
        self.wait_ms = wait_ms


class SessionStats(object):
    def __init__(self, session_id, model, start_ms, end_ms,
                 session_duration_ms, num_turns, total_work_ms,
                 total_wait_ms, total_between_ms, utilization,
                 turns, avg_turn_ms, median_turn_ms, max_turn_ms,
                 has_open_turn=False, is_running=False, is_idle=False,
                 project_name=""):
        self.session_id = session_id
        self.project_name = project_name
        self.model = model
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.session_duration_ms = session_duration_ms
        self.num_turns = num_turns
        self.total_work_ms = total_work_ms
        self.total_wait_ms = total_wait_ms
        self.total_between_ms = total_between_ms
        self.utilization = utilization
        self.turns = turns
        self.avg_turn_ms = avg_turn_ms
        self.median_turn_ms = median_turn_ms
        self.max_turn_ms = max_turn_ms
        self.has_open_turn = has_open_turn
        self.is_running = is_running
        self.is_idle = is_idle


class DayStats(object):
    def __init__(self, date, num_sessions, total_turns, total_work_ms,
                 total_wait_ms, total_between_ms, total_day_ms,
                 utilization, sessions):
        self.date = date
        self.num_sessions = num_sessions
        self.total_turns = total_turns
        self.total_work_ms = total_work_ms
        self.total_wait_ms = total_wait_ms
        self.total_between_ms = total_between_ms
        self.total_day_ms = total_day_ms
        self.utilization = utilization
        self.sessions = sessions


# ── Timestamp Parsing ────────────────────────────────────────────────────────


_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.?(\d*)")


def parse_ts_ms(s):
    """Parse ISO 8601 timestamp string to epoch milliseconds (UTC)."""
    m = _TS_RE.match(s)
    if not m:
        return None
    dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                  int(m.group(4)), int(m.group(5)), int(m.group(6)),
                  tzinfo=timezone.utc)
    frac = m.group(7)
    ms = int(frac.ljust(3, "0")[:3]) if frac else 0
    return int(calendar.timegm(dt.timetuple())) * 1000 + ms


# ── Session File Discovery ───────────────────────────────────────────────────


def discover_session_files(projects_dir, clip_start_ms=None):
    """Find session JSONL files, filtered by mtime."""
    results = []
    if not os.path.isdir(projects_dir):
        return results
    clip_start_epoch = clip_start_ms / 1000.0 if clip_start_ms else 0
    for project_name in os.listdir(projects_dir):
        project_path = os.path.join(projects_dir, project_name)
        if not os.path.isdir(project_path):
            continue
        for fname in os.listdir(project_path):
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(project_path, fname)
            if not os.path.isfile(fpath):
                continue
            if clip_start_epoch and os.path.getmtime(fpath) < clip_start_epoch:
                continue
            session_id = fname[:-6]  # strip .jsonl
            results.append((fpath, session_id, project_name))
    return results


# ── JSONL Parser ─────────────────────────────────────────────────────────────


def parse_session_file(path):
    """Parse a session JSONL file, extracting only timing-relevant records."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            if "forkedFrom" in d:
                continue

            rec_type = d.get("type")
            ts_str = d.get("timestamp")
            if not ts_str:
                continue
            ts_ms = parse_ts_ms(ts_str)
            if ts_ms is None:
                continue

            if rec_type == "user":
                msg = d.get("message", {})
                content = msg.get("content", "")
                is_tool_result = isinstance(content, list)
                is_meta = bool(d.get("isMeta"))
                records.append(Record(
                    kind="user", ts_ms=ts_ms,
                    is_tool_result=is_tool_result, is_meta=is_meta,
                ))

            elif rec_type == "assistant":
                msg = d.get("message", {})
                content = msg.get("content", [])
                tool_names = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_names.append(block.get("name", ""))
                model = msg.get("model")
                records.append(Record(
                    kind="assistant", ts_ms=ts_ms,
                    tool_names=tool_names, model=model,
                ))

            elif rec_type == "system":
                subtype = d.get("subtype")
                if subtype == "turn_duration":
                    records.append(Record(
                        kind="turn_duration", ts_ms=ts_ms,
                        duration_ms=d.get("durationMs"),
                    ))
                elif subtype == "stop_hook_summary":
                    records.append(Record(kind="stop_hook", ts_ms=ts_ms))
                elif subtype == "api_error":
                    records.append(Record(kind="api_error", ts_ms=ts_ms))

    return records


# ── Turn Derivation ──────────────────────────────────────────────────────────


def derive_turns(records):
    """Derive turns from native session records using a three-tier strategy.

    Returns (turns, has_open_turn, is_running).
    """
    turns = []
    n = len(records)
    i = 0
    turn_start_ms = None
    turn_start_idx = None
    last_activity_ts = None  # latest timestamp of any record within the current turn
    # Track wait time from user-input tools (AskUserQuestion, ExitPlanMode)
    pending_wait_tool_ts = None  # timestamp when wait tool was called
    total_wait_ms = 0
    model = None

    while i < n:
        r = records[i]

        if r.kind == "user" and not r.is_tool_result and not r.is_meta:
            # Real user prompt — ends previous turn (if open) and starts new one
            if turn_start_ms is not None:
                # Close previous turn via fallback (next user prompt, no turn_duration/stop_hook)
                if last_activity_ts and last_activity_ts > turn_start_ms:
                    end_ms = last_activity_ts
                    if pending_wait_tool_ts is not None:
                        total_wait_ms += end_ms - pending_wait_tool_ts
                        pending_wait_tool_ts = None
                    # If the gap is too large, the session was abandoned — discard turn
                    if end_ms - turn_start_ms > IDLE_THRESHOLD_MS:
                        dur = 0
                        end_ms = turn_start_ms
                    else:
                        dur = max(0, end_ms - turn_start_ms - total_wait_ms)
                else:
                    end_ms = turn_start_ms
                    dur = 0
                turns.append(Turn(start_ms=turn_start_ms, end_ms=end_ms,
                                  duration_ms=dur, wait_ms=total_wait_ms))
            turn_start_ms = r.ts_ms
            turn_start_idx = i
            last_activity_ts = None
            pending_wait_tool_ts = None
            total_wait_ms = 0
            i += 1
            continue

        if r.kind == "user" and r.is_tool_result:
            # Tool result — track as activity (includes interrupted tool returns)
            last_activity_ts = r.ts_ms
            if pending_wait_tool_ts is not None:
                total_wait_ms += r.ts_ms - pending_wait_tool_ts
                pending_wait_tool_ts = None
            i += 1
            continue

        if r.kind == "assistant":
            last_activity_ts = r.ts_ms
            if r.model:
                model = r.model
            # Check if this assistant message calls a wait tool
            if r.tool_names:
                for name in r.tool_names:
                    if name in WAIT_TOOLS:
                        pending_wait_tool_ts = r.ts_ms
                        break
            i += 1
            continue

        if r.kind == "turn_duration" and turn_start_ms is not None:
            # Gold standard: use durationMs directly
            end_ms = r.ts_ms
            raw_dur = r.duration_ms if r.duration_ms is not None else (end_ms - turn_start_ms)
            if pending_wait_tool_ts is not None:
                total_wait_ms += end_ms - pending_wait_tool_ts
                pending_wait_tool_ts = None
            dur = max(0, raw_dur - total_wait_ms)
            turns.append(Turn(start_ms=turn_start_ms, end_ms=end_ms,
                              duration_ms=dur, wait_ms=total_wait_ms))
            turn_start_ms = None
            last_assistant_ts = None
            total_wait_ms = 0
            i += 1
            continue

        if r.kind == "api_error" and turn_start_ms is not None:
            next_r = records[i + 1] if i + 1 < n else None
            next_is_recovery = (next_r is not None
                                and next_r.kind in ("api_error", "assistant", "turn_duration", "stop_hook")
                                and next_r.ts_ms - r.ts_ms < IDLE_THRESHOLD_MS)
            if not next_is_recovery:
                end_ms = r.ts_ms
                if pending_wait_tool_ts is not None:
                    total_wait_ms += end_ms - pending_wait_tool_ts
                    pending_wait_tool_ts = None
                dur = max(0, end_ms - turn_start_ms - total_wait_ms)
                turns.append(Turn(start_ms=turn_start_ms, end_ms=end_ms,
                                  duration_ms=dur, wait_ms=total_wait_ms))
                turn_start_ms = None
                last_activity_ts = None
                total_wait_ms = 0
            i += 1
            continue

        if r.kind == "stop_hook" and turn_start_ms is not None:
            # Check if a turn_duration follows immediately
            if i + 1 < n and records[i + 1].kind == "turn_duration":
                i += 1
                continue  # let turn_duration handle it
            # Tier 2: use stop_hook timestamp
            end_ms = r.ts_ms
            if pending_wait_tool_ts is not None:
                total_wait_ms += end_ms - pending_wait_tool_ts
                pending_wait_tool_ts = None
            dur = max(0, end_ms - turn_start_ms - total_wait_ms)
            turns.append(Turn(start_ms=turn_start_ms, end_ms=end_ms,
                              duration_ms=dur, wait_ms=total_wait_ms))
            turn_start_ms = None
            last_assistant_ts = None
            total_wait_ms = 0
            i += 1
            continue

        i += 1

    has_open_turn = turn_start_ms is not None
    open_turn_start_ms = turn_start_ms
    open_turn_wait_ms = total_wait_ms
    if pending_wait_tool_ts is not None and turn_start_ms is not None:
        open_turn_pending_wait_since = pending_wait_tool_ts
    else:
        open_turn_pending_wait_since = None
    return turns, has_open_turn, open_turn_start_ms, open_turn_wait_ms, open_turn_pending_wait_since


# ── Live Session Detection ───────────────────────────────────────────────────


def detect_live_sessions(sessions_dir):
    """Read ~/.claude/sessions/*.json and return sessions with live PIDs."""
    live = {}
    if not os.path.isdir(sessions_dir):
        return live
    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(sessions_dir, fname)
        try:
            with open(fpath) as f:
                d = json.load(f)
            pid = d.get("pid")
            sid = d.get("sessionId")
            if not pid or not sid:
                continue
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
            if alive:
                live[sid] = {
                    "pid": pid,
                    "cwd": d.get("cwd"),
                    "name": d.get("name", ""),
                    "status": d.get("status", ""),
                }
        except (json.JSONDecodeError, ValueError, IOError):
            continue
    return live


def read_session_names(sessions_dir):
    """Read ~/.claude/sessions/*.json and return a sid->name mapping."""
    names = {}
    if not os.path.isdir(sessions_dir):
        return names
    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(sessions_dir, fname)) as f:
                d = json.load(f)
            sid = d.get("sessionId")
            name = d.get("name", "")
            if sid and name:
                names[sid] = name
        except (json.JSONDecodeError, ValueError, IOError):
            continue
    return names


# ── Turn Clipping ────────────────────────────────────────────────────────────


def clip_turns(turns, start_ms, end_ms):
    """Clip turns to a time window, splitting cross-boundary turns."""
    clipped = []
    for t in turns:
        s = max(t.start_ms, start_ms)
        e = min(t.end_ms, end_ms)
        if s < e:
            ratio = (e - s) / (t.end_ms - t.start_ms) if t.end_ms > t.start_ms else 1.0
            clipped.append(Turn(
                start_ms=s, end_ms=e,
                duration_ms=max(0, int(t.duration_ms * ratio)),
                wait_ms=int(t.wait_ms * ratio),
            ))
    return clipped


# ── Session Stats ────────────────────────────────────────────────────────────


def compute_session_stats(session_id, records, live_sessions,
                          clip_start_ms=None, clip_end_ms=None, now_ms=None,
                          project_name=""):
    if not records:
        return None

    start_ms = records[0].ts_ms
    end_ms = records[-1].ts_ms

    is_live = session_id in live_sessions
    if is_live and now_ms is not None:
        end_ms = now_ms
    session_duration_ms = max(end_ms - start_ms, 0)

    # Find model from first assistant record
    model = None
    for r in records:
        if r.kind == "assistant" and r.model:
            model = r.model
            break

    (turns, has_open_turn,
     open_start_ms, open_wait_ms, open_pending_wait_since) = derive_turns(records)

    # Use session status from sessions/*.json to determine if actively working
    session_status = live_sessions.get(session_id, {}).get("status", "")
    is_running = is_live and session_status in ("busy", "shell")
    is_idle = is_live and not is_running

    # For running sessions with an open turn, add a partial turn up to now
    if is_running and has_open_turn and now_ms is not None and open_start_ms is not None:
        partial_end = now_ms
        partial_wait = open_wait_ms
        if open_pending_wait_since is not None:
            partial_wait += partial_end - open_pending_wait_since
        partial_dur = max(0, partial_end - open_start_ms - partial_wait)
        turns.append(Turn(start_ms=open_start_ms, end_ms=partial_end,
                          duration_ms=partial_dur, wait_ms=partial_wait))

    if clip_start_ms is not None and clip_end_ms is not None:
        turns = clip_turns(turns, clip_start_ms, clip_end_ms)

    total_work_ms = sum(t.duration_ms for t in turns)
    total_wait_ms = sum(t.wait_ms for t in turns)

    total_between_ms = 0
    for i in range(1, len(turns)):
        gap = turns[i].start_ms - turns[i - 1].end_ms
        total_between_ms += max(0, gap)

    utilization = total_work_ms / session_duration_ms if session_duration_ms > 0 else 0.0

    turn_durations = [t.duration_ms for t in turns]
    avg_turn = int(statistics.mean(turn_durations)) if turn_durations else 0
    median_turn = int(statistics.median(turn_durations)) if turn_durations else 0
    max_turn = max(turn_durations) if turn_durations else 0

    return SessionStats(
        session_id=session_id, model=model,
        start_ms=start_ms, end_ms=end_ms,
        session_duration_ms=session_duration_ms,
        num_turns=len(turns),
        total_work_ms=total_work_ms,
        total_wait_ms=total_wait_ms,
        total_between_ms=total_between_ms,
        utilization=utilization,
        turns=turns,
        avg_turn_ms=avg_turn,
        median_turn_ms=median_turn,
        max_turn_ms=max_turn,
        has_open_turn=has_open_turn if is_live else False,
        is_running=is_running,
        is_idle=is_idle,
        project_name=project_name,
    )


# ── Day Stats ────────────────────────────────────────────────────────────────


def compute_day_stats(sessions, tz=DEFAULT_TZ, window_ms=DAY_MS, multiplier=1.0):
    total_work = sum(s.total_work_ms for s in sessions)
    total_wait = sum(s.total_wait_ms for s in sessions)
    total_between = sum(s.total_between_ms for s in sessions)
    total_turns = sum(s.num_turns for s in sessions)

    date_str = ""
    if sessions and sessions[0].start_ms is not None:
        dt = datetime.fromtimestamp(sessions[0].start_ms / 1000, tz=tz)
        date_str = dt.strftime("%Y-%m-%d")

    return DayStats(
        date=date_str,
        num_sessions=len(sessions),
        total_turns=total_turns,
        total_work_ms=total_work,
        total_wait_ms=total_wait,
        total_between_ms=total_between,
        total_day_ms=window_ms,
        utilization=total_work * multiplier / window_ms if window_ms > 0 else 0.0,
        sessions=sessions,
    )


# ── Formatting ───────────────────────────────────────────────────────────────


def format_duration(ms):
    if ms <= 0:
        return "0s"
    total_seconds = ms // 1000
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    if h > 0:
        return "{}h {:02d}m {:02d}s".format(h, m, s)
    elif m > 0:
        return "{}m {:02d}s".format(m, s)
    else:
        return "{}s".format(s)


def _bar(score, width=20):
    filled = int(min(score, 100)) * width // 100
    return "█" * filled + "░" * (width - filled)


# ── Heatmap ──────────────────────────────────────────────────────────────────


_IDLE_CHAR = " "
_HEAT_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]


_HEATMAP_BG = "\033[48;5;236m"
_HEATMAP_RESET = "\033[0m"


def _render_heatmap(values):
    max_v = max(values) if values else 0
    if max_v == 0:
        return _HEATMAP_BG + _IDLE_CHAR * len(values) + _HEATMAP_RESET
    chars = []
    for v in values:
        if v == 0:
            chars.append(_IDLE_CHAR)
        else:
            t = v / max_v
            level = min(int(t * len(_HEAT_CHARS)), len(_HEAT_CHARS) - 1)
            chars.append(_HEAT_CHARS[level])
    return _HEATMAP_BG + "".join(chars) + _HEATMAP_RESET


def _compute_bucket_concurrency(sessions, start_ms, end_ms, num_buckets):
    bucket_ms = (end_ms - start_ms) / num_buckets
    if bucket_ms <= 0:
        return [0] * num_buckets
    counts = [0] * num_buckets
    for s in sessions:
        active = set()
        for t in s.turns:
            b_start = max(0, int((t.start_ms - start_ms) / bucket_ms))
            b_end = min(num_buckets - 1, int((t.end_ms - start_ms) / bucket_ms))
            for b in range(b_start, b_end + 1):
                active.add(b)
        for b in active:
            counts[b] += 1
    return counts


def _make_legend(max_v):
    if max_v == 0:
        return ""
    n = len(_HEAT_CHARS)
    # Group values by which character they map to
    groups = [[] for _ in range(n)]
    for v in range(1, max_v + 1):
        t = v / max_v
        level = min(int(t * n), n - 1)
        groups[level].append(v)
    parts = []
    for level, vals in enumerate(groups):
        if not vals:
            continue
        ch = _HEAT_CHARS[level]
        if vals[0] == vals[-1]:
            parts.append("{}={}".format(ch, vals[0]))
        else:
            parts.append("{}={}-{}".format(ch, vals[0], vals[-1]))
    return "  ".join(parts)


def _format_heatmap_section(sessions, start_ms, end_ms, tz):
    width = 64
    lines = []
    dt_start = datetime.fromtimestamp(start_ms / 1000, tz=tz)
    base_hour = (dt_start.hour // 8) * 8
    seg_dt = dt_start.replace(hour=base_hour, minute=0, second=0, microsecond=0)
    dt_end = datetime.fromtimestamp(end_ms / 1000, tz=tz)

    segments = []
    while seg_dt < dt_end:
        seg_next = seg_dt + timedelta(hours=8)
        segments.append((seg_dt, seg_next))
        seg_dt = seg_next

    global_max = 0
    for seg_start_dt, seg_end_dt in segments:
        s_ms = int(seg_start_dt.timestamp() * 1000)
        e_ms = int(seg_end_dt.timestamp() * 1000)
        end_h = seg_end_dt.strftime("%H") if seg_end_dt.hour != 0 else "24"
        label = seg_start_dt.strftime("%m/%d %H") + "-" + end_h
        counts = _compute_bucket_concurrency(sessions, s_ms, e_ms, width)
        bar = _render_heatmap(counts)
        max_c = max(counts) if counts else 0
        global_max = max(global_max, max_c)
        lines.append("  {} {}".format(label, bar))

    if global_max > 0:
        lines.append("  Legend: {}  (peak: {})".format(_make_legend(global_max), global_max))

    return lines


# ── Report Formatting ────────────────────────────────────────────────────────


def format_report(day, tz=DEFAULT_TZ, clip_start_ms=None, clip_end_ms=None, multiplier=1.0, session_names=None):
    util_pct = round(day.utilization * 100, 2)
    lines = []
    lines.append("CC Utilization Dashboard — {}".format(day.date))
    lines.append("=" * 50)
    lines.append("")

    lines.append("Daily Summary")
    lines.append("  Window:              {}".format(format_duration(day.total_day_ms)))
    busy = sum(1 for s in day.sessions if s.is_running)
    idle = sum(1 for s in day.sessions if s.is_idle)
    parts = []
    if busy > 0:
        parts.append("{} busy".format(busy))
    if idle > 0:
        parts.append("{} idle".format(idle))
    status_str = " ({})".format(", ".join(parts)) if parts else ""
    lines.append("  Sessions:            {}{}".format(day.num_sessions, status_str))
    lines.append("  Total turns:         {}".format(day.total_turns))
    lines.append("  CC work time:        {}".format(format_duration(day.total_work_ms)))
    lines.append("  Idle (waiting):      {}".format(format_duration(day.total_wait_ms)))
    lines.append("  Idle (between):      {}".format(format_duration(day.total_between_ms)))
    mult_str = " ({:.10g}x)".format(multiplier) if multiplier != 1.0 else ""
    lines.append("  Utilization:         {:.2f}%{}".format(util_pct, mult_str))
    lines.append("")

    if day.sessions and clip_start_ms is not None and clip_end_ms is not None:
        lines.append("Concurrency Heatmap")
        lines.extend(_format_heatmap_section(day.sessions, clip_start_ms, clip_end_ms, tz))
        lines.append("")

    if day.sessions:
        lines.append("Session Breakdown")
        fmt = "  {:<4} {:<12} {:12}  {:>5}  {:>13}  {}"
        names = session_names or {}
        lines.append(fmt.format("#", "DIR", "NAME/SID", "TURNS", "WORK", ""))
        n = 0
        for s in sorted(day.sessions, key=lambda s: s.total_work_ms, reverse=True):
            if s.num_turns == 0 and not s.has_open_turn:
                continue
            n += 1
            name = names.get(s.session_id, "")
            sid = (name or s.session_id[:8])[:12]
            work = format_duration(s.total_work_ms)
            pname = s.project_name.strip("-")
            parts = pname.rsplit("-", 2) if pname else ["?"]
            dir_label = "-".join(parts[-2:])[:12] if len(parts) >= 2 else parts[0][:12]
            if s.is_running:
                status = "(busy)"
            elif s.is_idle:
                status = "(idle)"
            else:
                status = ""
            lines.append(fmt.format("#{}".format(n), dir_label, sid, s.num_turns, work, status))
        lines.append("")

    lines.append("Score")
    mult_str2 = " ({:.10g}x)".format(multiplier) if multiplier != 1.0 else ""
    lines.append("  Utilization:  {:>6.2f}%{}  {}".format(util_pct, mult_str2, _bar(util_pct)))
    lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _day_bounds_ms(date_str, tz):
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    return int(day_start.timestamp() * 1000), int(day_end.timestamp() * 1000)


def _parse_tz(tz_str):
    if tz_str.upper() == "UTC":
        return timezone.utc
    tz_str = tz_str.replace("UTC", "").replace("utc", "")
    if ":" in tz_str:
        parts = tz_str.split(":")
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        if hours < 0:
            minutes = -minutes
        return timezone(timedelta(hours=hours, minutes=minutes))
    return timezone(timedelta(hours=int(tz_str)))


def main():
    parser = argparse.ArgumentParser(
        description="Claude Code utilization analyzer — reads native session files")
    parser.add_argument("--today", action="store_true", help="Show stats for today (full 24h)")
    parser.add_argument("--realtime", action="store_true",
                        help="Use midnight-to-now window instead of full 24h")
    parser.add_argument("--date", help="Show stats for a specific date (YYYY-MM-DD)")
    parser.add_argument("--range", help="Rolling window: Nd or Nh (e.g., 7d, 3h)")
    parser.add_argument("--session", help="Filter by session ID (prefix match)")
    parser.add_argument("--project", help="Filter by project path (substring match)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--field", metavar="FIELD",
                        help="Output a single field value")
    parser.add_argument("--multiplier", type=float, default=3.0,
                        help="Utilization multiplier (default: 3.0)")
    parser.add_argument("--tz", help="Timezone offset (e.g., +9, -5, UTC)")
    parser.add_argument("--projects-dir",
                        default=os.path.expanduser("~/.claude/projects"),
                        help="Projects directory (default: ~/.claude/projects)")
    parser.add_argument("--sessions-dir",
                        default=os.path.expanduser("~/.claude/sessions"),
                        help="Sessions directory (default: ~/.claude/sessions)")
    args = parser.parse_args()

    tz = _parse_tz(args.tz) if args.tz else DEFAULT_TZ
    now_ms = int(datetime.now(tz).timestamp() * 1000)
    today_str = datetime.now(tz).strftime("%Y-%m-%d")

    # Determine time window
    clip_start_ms = None
    clip_end_ms = None

    if args.today:
        clip_start_ms, clip_end_ms = _day_bounds_ms(today_str, tz)
        if args.realtime:
            clip_end_ms = now_ms
    elif args.date:
        clip_start_ms, clip_end_ms = _day_bounds_ms(args.date, tz)
        if args.realtime and args.date == today_str:
            clip_end_ms = now_ms
    elif args.range:
        r = args.range
        clip_end_ms = now_ms
        if r.endswith("h"):
            clip_start_ms = now_ms - int(r.rstrip("h")) * 3600 * 1000
        else:
            clip_start_ms = now_ms - int(r.rstrip("d")) * DAY_MS
    else:
        clip_start_ms, clip_end_ms = _day_bounds_ms(today_str, tz)
        if args.realtime:
            clip_end_ms = now_ms

    # Discover session files
    session_files = discover_session_files(args.projects_dir, clip_start_ms)

    if args.project:
        session_files = [(p, sid, proj) for p, sid, proj in session_files
                         if args.project in proj]

    if args.session:
        session_files = [(p, sid, proj) for p, sid, proj in session_files
                         if sid.startswith(args.session)]

    # Detect live sessions and read session names
    live_sessions = detect_live_sessions(args.sessions_dir)
    session_names = read_session_names(args.sessions_dir)

    # Parse and compute stats per session
    sessions = []
    for fpath, session_id, project_name in session_files:
        records = parse_session_file(fpath)
        if not records:
            continue
        stats = compute_session_stats(
            session_id, records, live_sessions,
            clip_start_ms, clip_end_ms, now_ms,
            project_name=project_name)
        if stats is not None:
            sessions.append(stats)

    window_includes_now = clip_end_ms is not None and clip_end_ms >= now_ms
    sessions = [s for s in sessions
                if s.num_turns > 0 or ((s.is_running or s.is_idle) and window_includes_now)]
    sessions.sort(key=lambda s: s.start_ms)

    window_ms = clip_end_ms - clip_start_ms if clip_start_ms and clip_end_ms else DAY_MS
    day = compute_day_stats(sessions, tz, window_ms, args.multiplier)

    if args.today:
        day.date = today_str
    elif args.date:
        day.date = args.date
    elif args.range:
        day.date = "last {}".format(args.range)
    elif not day.date:
        day.date = today_str

    output_data = {
        "date": day.date,
        "window_ms": day.total_day_ms,
        "window_fmt": format_duration(day.total_day_ms),
        "sessions": day.num_sessions,
        "turns": day.total_turns,
        "work_ms": day.total_work_ms,
        "work_fmt": format_duration(day.total_work_ms),
        "wait_ms": day.total_wait_ms,
        "wait_fmt": format_duration(day.total_wait_ms),
        "between_ms": day.total_between_ms,
        "between_fmt": format_duration(day.total_between_ms),
        "utilization": round(day.utilization * 100, 2),
        "utilization_multiplier": args.multiplier,
        "active": sum(1 for s in sessions if s.is_running),
    }

    if args.field:
        val = output_data.get(args.field)
        if val is None:
            print("Unknown field: {}. Available: {}".format(
                args.field, ", ".join(output_data.keys())), file=sys.stderr)
            sys.exit(1)
        print(val)
    elif args.json:
        print(json.dumps(output_data, indent=2))
    else:
        print(format_report(day, tz, clip_start_ms, clip_end_ms, args.multiplier, session_names))


if __name__ == "__main__":
    main()

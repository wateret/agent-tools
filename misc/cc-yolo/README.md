# cc-yolo — Claude Code Utilization Stats

Reads Claude Code's native session files at `~/.claude/projects/` and reports how much wall-clock time you spent with Claude actually working vs. idle. No plugin, no hooks, no daemon — just one Python script.

## What's here

- [`stats.py`](./stats.py) — the analyzer. Outputs a daily dashboard, JSON, or a single field.
- [`tmux-status.sh`](./tmux-status.sh) — tmux `status-right` segment that calls `stats.py --json` and renders a colored utilization indicator. Caches results for a configurable TTL.
- [`test_stats.py`](./test_stats.py) — unit tests for the analyzer (run with `python3 -m unittest test_stats`).

## Quick use

```bash
# Today, default 3x multiplier
python3 stats.py

# Last 7 days
python3 stats.py --range 7d

# JSON for scripting
python3 stats.py --json

# Single field (great for shell prompts)
python3 stats.py --field utilization
```

See `python3 stats.py --help` for all flags (`--date`, `--session`, `--project`, `--tz`, `--multiplier`, etc.).

## tmux integration

Add to your `~/.tmux.conf`:

```tmux
set -g status-right '#(/path/to/misc/cc-yolo/tmux-status.sh --mode time --theme dark --sessions)'
```

Flags:

- `--mode pct|time` — show utilization percent or work time (default: `time`)
- `--theme dark|light` — color palette (default: `dark`)
- `--ttl SECONDS` — cache TTL (default: `5`)
- `--sessions` — append active session count
- `--turns` — append today's turn count

`--sessions` and `--turns` may be passed in any order; output order matches flag order.

## Prerequisites

- Python 3 (no third-party deps)
- For `tmux-status.sh`: `jq` and `awk`

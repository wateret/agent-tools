#!/usr/bin/env bash
# Outputs Claude Code icons for panes in a given tmux window.
# Usage: claude-icons.sh <session:window>
#
# Reads ~/.claude/sessions/<pid>.json for active sessions and their status,
# then maps each session PID to a tmux pane via PPID chain.

WINDOW_TARGET="$1"
[ -z "$WINDOW_TARGET" ] && exit 0

NERD_FONT="${NERD_FONT:-1}"

if [ "$NERD_FONT" -eq 1 ]; then
  ICON_IDLE="󰚩"
  ICON_BUSY="󱚝"
  ICON_WAITING="󱚟"
  ICON_SHELL="󱚝"
else
  ICON_IDLE="."
  ICON_BUSY="!"
  ICON_WAITING="?"
  ICON_SHELL="~"
fi
CACHE_FILE="/tmp/tmux-claude-icons-${USER}"
CACHE_TTL=5
SESSIONS_DIR="${HOME}/.claude/sessions"

COLOR_BUSY="#ff79c6"
COLOR_IDLE="#6272a4"
COLOR_WAITING="#f1fa8c"
COLOR_SHELL="#bd93f9"

needs_refresh=1
if [ -f "$CACHE_FILE" ]; then
  now=$(date +%s)
  mtime=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
  if [ $((now - mtime)) -lt "$CACHE_TTL" ]; then
    needs_refresh=0
  fi
fi

if [ "$needs_refresh" -eq 1 ]; then
  tmp="${CACHE_FILE}.$$"

  # Build pane_pid -> window_target map, and parent lookup in one awk pass
  {
    tmux list-panes -a -F 'PANE #{session_name}:#{window_index} #{pane_index} #{pane_pid}' 2>/dev/null
    echo "ENDPANES"
    ps -eo pid=,ppid= 2>/dev/null
  } | awk -v sessions_dir="$SESSIONS_DIR" '
    /^PANE / { pane_win[$4] = $2; pane_idx[$4] = $3; next }
    /^ENDPANES$/ { next }
    { parent[$1+0] = $2+0 }
    END {
      # For each session file, walk up PPID chain to find owning pane
      cmd = "ls " sessions_dir "/*.json 2>/dev/null"
      while ((cmd | getline f) > 0) {
        # Extract PID from filename
        n = split(f, parts, "/")
        gsub(/\.json$/, "", parts[n])
        pid = parts[n] + 0
        if (pid == 0) continue
        if (!(pid in parent)) continue

        # Skip non-interactive sessions (subagents, etc.)
        getline content < f
        close(f)
        if (content !~ /"kind"[ \t]*:[ \t]*"interactive"/) continue
        if (content !~ /"entrypoint"[ \t]*:[ \t]*"cli"/) continue

        # Extract status
        status = "idle"
        if (match(content, /"status"[ \t]*:[ \t]*"[^"]*"/)) {
          s = substr(content, RSTART, RLENGTH)
          gsub(/.*"status"[ \t]*:[ \t]*"/, "", s)
          gsub(/"$/, "", s)
          status = s
        }

        # Walk up parent chain
        cur = pid
        for (i = 0; i < 50; i++) {
          if (cur in pane_win) {
            print pane_win[cur] " " pane_idx[cur] " " status
            break
          }
          if (!(cur in parent) || parent[cur] == cur || cur <= 1) break
          cur = parent[cur]
        }
      }
      close(cmd)
    }
  ' | sort -k1,1 -k2,2n > "$tmp"

  mv -f "$tmp" "$CACHE_FILE" 2>/dev/null
fi

if [ -f "$CACHE_FILE" ]; then
  output=""
  while IFS=' ' read -r wt _pane_idx status; do
    [ "$wt" != "$WINDOW_TARGET" ] && continue
    if [ "$status" = "busy" ]; then
      output="${output}#[fg=${COLOR_BUSY}]${ICON_BUSY}"
    elif [ "$status" = "waiting" ]; then
      output="${output}#[fg=${COLOR_WAITING}]${ICON_WAITING}"
    elif [ "$status" = "shell" ]; then
      output="${output}#[fg=${COLOR_SHELL}]${ICON_SHELL}"
    else
      output="${output}#[fg=${COLOR_IDLE}]${ICON_IDLE}"
    fi
  done < "$CACHE_FILE"
  if [ -n "$output" ]; then
    printf ' %s#[fg=default]' "$output"
  fi
fi

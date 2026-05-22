#!/usr/bin/env bash
exec 0</dev/null
if [ -n "$TMUX_PANE" ]; then
  WIN=$(tmux display-message -t "$TMUX_PANE" -p '#{window_id}' 2>/dev/null)
  ZOOMED=$(tmux display-message -t "$TMUX_PANE" -p '#{window_zoomed_flag}' 2>/dev/null)
  ACTIVE_WIN=$(tmux display-message -p '#{window_id}' 2>/dev/null)
  if [ -n "$WIN" ] && [ "$WIN" != "$ACTIVE_WIN" ]; then
    # Use a temporary pane to trigger bell instead of writing to TTY directly.
    # Writing BEL to TTY can corrupt CSI escape sequences due to race conditions.
    tmux split-window -t "$WIN" -d -l 0 "printf '\a'" 2>/dev/null
    # split-window unzooms the window; re-zoom if it was zoomed before
    if [ "$ZOOMED" = "1" ]; then
      sleep 0.1
      tmux resize-pane -t "$TMUX_PANE" -Z 2>/dev/null
    fi
  fi
fi

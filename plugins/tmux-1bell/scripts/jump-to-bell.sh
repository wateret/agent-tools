#!/bin/sh
cur=$(tmux display-message -p '#I')
bell_windows=$(tmux list-windows -F '#I #F' 2>/dev/null | grep '!' | cut -d ' ' -f 1)
if [ -z "$bell_windows" ]; then
  tmux display-message "No bell window"
  exit 0
fi
w=$(printf '%s\n' $bell_windows | awk -v cur="$cur" '$1 > cur { print; found=1 }' | head -n 1)
if [ -z "$w" ]; then
  w=$(printf '%s\n' $bell_windows | head -n 1)
fi
tmux select-window -t "$w" 2>/dev/null || tmux display-message "Failed to switch to window $w"

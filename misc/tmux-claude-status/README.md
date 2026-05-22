# Claude Status for tmux status bar

Shows a colored Claude Code icon in the tmux status bar for each window running an active Claude session — color indicates the session state.

| Color | State |
|-------|-------|
| Pink | busy |
| Orange | running shell command |
| Yellow | waiting for input |
| Blue/dim | idle |

## tmux.conf

Clone this repo and add to your `window-status-format`:

```
set -g window-status-format "... #(s=\$HOME/path/to/agent-tools/misc/tmux-claude-status/claude-status.sh; test -x \$s && \$s '#{session_name}:#{window_index}') "
set -g window-status-current-format "... #(s=\$HOME/path/to/agent-tools/misc/tmux-claude-status/claude-status.sh; test -x \$s && \$s '#{session_name}:#{window_index}') "
```

If the script is not found the format string silently outputs nothing.

The script caches results for 5 seconds to avoid hammering the filesystem on every status bar refresh.

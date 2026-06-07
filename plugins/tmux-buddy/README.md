# tmux-buddy

Helps Claude Code work alongside you inside tmux — the agent runs commands in other panes you can watch live, you can interject by typing directly, and interactive prompts (sudo, 2FA) get answered by you in the live pane.

## What's inside

- **Skills**: [`branch-pane`](./skills/branch-pane/) (fork this session into a new pane), [`work-in-pane`](./skills/work-in-pane/) (patterns the agent uses for shell work in other panes).
- **Scripts**: [`tmux-is-idle`](./scripts/tmux-is-idle), [`tmux-wait`](./scripts/tmux-wait), [`tmux-run`](./scripts/tmux-run), [`tmux-capture`](./scripts/tmux-capture). See each file's header for usage.

## Prerequisites

- tmux
- `claude` CLI on `PATH` (for `branch-pane`)
- macOS or Linux

# tmux-bell — Claude Code Plugin

Rings the terminal bell when Claude Code needs your attention — but only when the Claude pane is in a **different tmux window** than the one you're currently looking at. So you get notified when you've context-switched away, and stay quiet when you're already watching.

## What triggers a bell

Using Claude Hooks. The bell only fires when Claude's pane is in a **different** tmux window than the active one — see [scripts/bell.sh](./scripts/bell.sh) for the detection logic.

## Prerequisites

- tmux
- A terminal that surfaces the bell (visual flash, audio, or window-urgency hint — your choice via your terminal's bell settings)

# agent-tools

Tools and configurations for AI coding agents (Claude Code only for now)

## Tools

| Tool | Type | Description |
|------|------|-------------|
| [tmux-1bell](./plugins/tmux-1bell/) | Claude Code plugin | Rings the terminal bell when Claude needs attention |
| [vscode-ssh-link](./misc/vscode-ssh-link/) | Misc | Outputs clickable `vscode://` links in the terminal when Claude edits or mentions files |
| [cc-yolo](./misc/cc-yolo/) | Misc | Claude Code utilization stats — active time and so on, from session files |
| [tmux-claude-status ↗](https://github.com/wateret/tmux-claude-status) | tmux plugin | Shows Claude Code session status icons in the tmux status bar |
| [tmux-claude-finder ↗](https://github.com/wateret/tmux-claude-finder) | tmux plugin | Search across live Claude Code sessions in tmux and jump to the matching pane |
| [tmux-resurrect-agents ↗](https://github.com/wateret/tmux-resurrect-agents) | tmux plugin | Save and restore Claude Code and Codex sessions across tmux-resurrect cycles |

*Each tmux plugin lives in its own repository, following the TPM convention.*

## Install a Claude Code plugin

```bash
claude plugins marketplace add https://github.com/wateret/agent-tools.git # Add marketplace
claude plugin install tmux-1bell@wateret-agent-tools # Install a plugin
```

## Install a misc tool

See each tool's README for details.

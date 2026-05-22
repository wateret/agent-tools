# agent-tools

Tools and configurations for AI coding agents (Claude Code only for now)

## Plugins

| Plugin | Description |
|--------|-------------|
| [tmux-1bell](./plugins/tmux-1bell/) | Rings the terminal bell when Claude needs attention |

### Install

```bash
claude plugins marketplace add https://github.com/wateret/agent-tools.git # Add marketplace
claude plugin install tmux-1bell@wateret-agent-tools # Install a plugin
```

## Misc tools

Non-plugin tools — manual install, see each README for details.

| Tool | Description |
|------|-------------|
| [vscode-ssh-link](./misc/vscode-ssh-link/) | Outputs clickable `vscode://` links in the terminal when Claude edits or mentions files |
| [cc-yolo](./misc/cc-yolo/) | Claude Code utilization stats — active vs idle time from session files |
| [tmux-claude-status](./misc/tmux-claude-status/) | Shows colored Claude session status icons in the tmux status bar |

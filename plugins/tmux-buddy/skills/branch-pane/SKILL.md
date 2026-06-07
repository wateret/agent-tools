---
name: branch-pane
description: >
  Branch the current Claude session into a new tmux pane. Splits the current
  tmux window and launches `claude --resume <current-session-id> --fork-session`
  in the new pane so you can explore an alternate direction without disturbing
  the conversation here. Use when the user says "branch this session",
  "fork into a new pane", "/branch-pane", "split and continue elsewhere",
  or wants to try a what-if line of work in parallel.
---

# branch-pane

Spawn a forked copy of the **current** Claude session in a freshly-split tmux
pane. The original session here keeps running; the new pane gets its own
session id (via `--fork-session`) so anything you do there does not pollute
this transcript.

## When to use

- User wants to try an alternate approach without losing the current state.
- User wants to keep the main thread on task A while exploring task B in a
  side pane.
- User explicitly invokes `/branch-pane` or asks to "branch into a new
  pane / fork this session into another pane".

## Preconditions

This skill only works when **all** of the following are true. If any fails,
stop and tell the user — do **not** try to work around it.

1. We are inside tmux. Verify with `[ -n "$TMUX" ]` or by running
   `tmux display-message -p '#S'` and getting a non-empty answer.
2. The current Claude session id is available in `$CLAUDE_CODE_SESSION_ID`.
3. The `claude` binary is on `PATH` (`command -v claude`).

## What to do

Run a single tmux command that splits **this** pane (not whatever pane is
active at the moment of the call) and starts the forked Claude inside it.
Default split is **horizontal (top/bottom, `-v`)**.

```bash
tmux split-window -v -t "$TMUX_PANE" "claude --resume '$CLAUDE_CODE_SESSION_ID' --fork-session"
```

Notes:

- **Always pass `-t "$TMUX_PANE"`.** `$TMUX_PANE` is set by tmux in the
  environment of every process running inside a pane and stays pinned to
  this Claude process even if the user moves focus to a different pane
  between the time you read the skill and the time you run the command.
  Without `-t`, `split-window` targets whatever pane is currently active —
  which may not be ours.
- `--fork-session` plus `--resume <id>` is the supported way to start a child
  session that inherits this conversation but gets a new id. This is preferred
  over plain `--resume` because two live panes sharing one session id will
  fight over the transcript.
- The new pane inherits the current working directory by default — that is
  usually what we want, since the forked conversation should see the same
  repo state.
- Do not pass an initial prompt. The user types whatever they want in the
  new pane.

### Optional split-direction argument

If the user passes an argument when invoking the skill, honor it:

| Arg              | Behavior                                                     |
|------------------|--------------------------------------------------------------|
| (none)           | `tmux split-window -v -t "$TMUX_PANE"` — horizontal (default)|
| `-h` / `right`   | `tmux split-window -h -t "$TMUX_PANE"` — vertical (left/right)|
| `-v` / `below`   | `tmux split-window -v -t "$TMUX_PANE"` — horizontal          |

## After launching

Report to the user, in one short sentence, that the forked session has been
started in a new pane and that this pane is unaffected. Do not narrate the
tmux command.

## What this skill does NOT do

- Does not create a git branch or worktree. The name "branch" refers to
  branching the **conversation**, not the repo. If the user wants a git
  branch, use a separate flow.
- Does not pass any initial prompt to the forked session.
- Does not change the working directory. The new pane starts in the same cwd
  as the current pane.
- Does not kill or detach the current pane.

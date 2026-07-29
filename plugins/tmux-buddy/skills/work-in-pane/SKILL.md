---
name: work-in-pane
description: >
  Reference for working with tmux panes alongside the human. Consult when
  the user asks to run or monitor a command in another pane, split work
  across panes (e.g. build + test), wait for a build, watch output, or
  coordinate work the human can watch live. Covers single-pane workflow,
  multi-pane orchestration, signal-based completion (tmux-run) vs idle
  polling (tmux-wait), human interjection, and interactive prompts.
---

# work-in-pane

This is a **reference**, not an action. Consult it when the user wants you to
do work in tmux panes other than your own — building, testing, tailing logs,
running a dev server, anything they can watch live.

The point of this plugin is that the human and the agent share one tmux
workspace. The human sees what you do, can drop into any pane to type
directly, and can answer interactive prompts (sudo password, 2FA) in the
live pane. Keep that picture in mind while you work.

## Survey first

Before sending anything anywhere, see what is already there.

```bash
tmux list-panes -F '#{pane_index} #{pane_id} #{pane_active} #{pane_current_command} #{pane_width}x#{pane_height}'
```

This lists panes in the **current window only**, which is the scope you
should stay inside — the human is looking at this window. Do not target
panes in other windows; they are not visible to the user right now.

From the output, note:

- Which pane is yours (`pane_active` is `1`).
- Which panes look idle (running `zsh`/`bash` with nothing else).
- Which panes are running something already (`make`, `vim`, dev server, …).
- How much room you have to split.

`tmux-is-idle <pane_id>` confirms whether a pane is at a shell prompt:
exit `0` = idle, `1` = busy, `2` = dead.

### How to refer to panes when talking to the human

The human sees panes by their **pane index** — the small number in the
status line, e.g. `0`, `1`, `2`. The `%<n>` form (`%17` etc.) is a tmux
internal id they don't normally look at.

When mentioning a pane in chat, **lead with the pane index** and put the
`%id` in parentheses if you want to keep it traceable:

> "running the build in pane 1 (`%17`)"
> "pane 2 (`%19`) is waiting on a sudo password"

Bare `%17` in user-facing prose is wrong — say `pane 1 (%17)` or just
`pane 1`. Inside scripts and tmux commands, **keep using `%id`** (it's
stable, see below); the index-first rule is for what you say to the user.

### How to interpret a pane number the human gives you

`%` prefix is the switch:

- **No `%`** ("pane 1") → pane index. Resolve to a `%id` in the current
  window before passing to scripts: `tmux list-panes -F '#{pane_index} #{pane_id}'`.
- **With `%`** ("%17") → pane id, use as-is.

Ambiguous? Ask, don't guess.

## Pattern A — reuse an empty pane

If the survey shows an idle pane the user does not seem to be using, send
your command there. No new split needed.

```bash
tmux-is-idle %15 && tmux-run %15 "make"
```

## Pattern B — split a new pane

If no good pane exists, or the work should be isolated, split. Capture the
new pane id from `-P -F`:

```bash
pane=$(tmux split-window -h -P -F '#{pane_id}')
tmux-run "$pane" "make"
```

`-h` splits left/right (new pane on the right), `-v` splits top/bottom
(new pane on the bottom). The new pane inherits the current working
directory.

The pane id (`%17` etc.) is stable for the life of that pane. Hold it as a
shell variable for the rest of the turn. If you will need it across many
turns, write it into your plan or a task description — there is no
registry / state file in this plugin by design.

## Pattern C — multiple panes for one request

When the user asks for several things in parallel ("build and test"), split
once per workload and dispatch:

```bash
build_pane=$(tmux split-window -h -P -F '#{pane_id}')
test_pane=$(tmux split-window -v -t "$build_pane" -P -F '#{pane_id}')

tmux-run "$build_pane" "make" &
tmux-run "$test_pane" "pytest" &
wait

# Now read both:
echo "--- build ---"
tmux-capture "$build_pane" 200
echo "--- test ---"
tmux-capture "$test_pane" 200
```

If the user later says "rerun the build", reuse `$build_pane` — do not
split again.

## Sending commands and detecting completion

Two paths, depending on who started the command.

**Commands you send → `tmux-run`.** Signal-based, zero polling latency. It
appends `; tmux wait-for -S <channel>` to the command, so the human will
see that suffix in their pane. That is expected; mention it in passing if
they ask. `tmux-run` exits as soon as the command returns. It does not
capture exit code — read results with `tmux capture-pane`.

`tmux-run` refuses (exit `3`) if the target pane is busy — a foreground
command would swallow our keystrokes into its stdin, and a subsequent
^C from the human cancels that input line, leaving our wait-for channel
unsignaled forever (silent hang). Set `TMUX_RUN_FORCE=1` to bypass when
you genuinely want the queued-after-current behavior.

When you hit exit `3`, **surface it to the user**. Capture the target
pane to show what's running there (so they know *why* it was busy), then
ask whether to wait, target a different pane, or override. Don't silently
move on — the user can't see the refusal otherwise.

**Commands the human typed, or anything you did not wrap → `tmux-wait`.**
Polls `tmux-is-idle` once per second (configurable via
`TMUX_WAIT_INTERVAL`). Default timeout 300 s, override with the second
arg: `tmux-wait %15 600`. Exit codes: `0` became idle, `1` timed out,
`2` pane died.

Before sending anything, **check that the pane is at a prompt**:
`tmux-is-idle <pane>`. A `busy` pane may be the human typing, or a command
already running — sending into either case is destructive.

## Long-running tmux-run — wrap in a background Bash task, never `&`

`tmux-run` blocks until its wait-for channel fires. For long-running
work (build, test loops, anything taking minutes) two patterns are
correct:

- **Foreground**: just call `tmux-run %X "<cmd>"` from a normal Bash
  invocation. Fine when total wall-time fits the agent's tool timeout.

- **Background task**: invoke `tmux-run %X "<cmd>"` inside a Bash tool
  call with `run_in_background: true`. The wrapper stays as the task's
  foreground child; when the pane signals the channel, the wrapper
  exits, the task exits, and you get a task-notification.

The **anti-pattern** is `tmux-run %X "<cmd>" &` from a foreground Bash.
The wrapper detaches and reparents to PID 1; nothing waits on it, no
notification fires when the pane finishes. You will not learn the
command is done until you actively check.

## Reading output

```bash
tmux-capture %17 200
```

`tmux-capture` is a thin wrapper over `tmux capture-pane` with sensible
defaults: 200 lines of scrollback, ANSI escape sequences stripped, runs
of blank lines collapsed. Pass a different line count as the second
argument when you need more or less. If you need raw output (e.g. ANSI
preserved for some reason), drop down to
`tmux capture-pane -p -t <pane> -S -<lines>` directly.

## The human can interject

Capture is honest about what's on screen. If you see commands or output
you did not produce, the human typed something directly. Don't ignore it
or reset the pane — incorporate what they did into the next step.

Likewise, if you sent `tmux-run %17 "make install"` and it has been
running for a while, capture the pane. If the bottom shows something like
`Password:`, `[sudo] password for user:`, or a 2FA prompt, **do not try to
type the answer with `send-keys`**. Tell the user out loud, naming the
pane by its index (with `%id` in parens if useful):

> "pane 1 (`%17`) is waiting on a sudo password — please type it in that pane."

Then re-enter the wait. `tmux-run` will return when the command actually
finishes. If you wrapped with `tmux-run` but want to bail because the
pane is stuck on a prompt the human won't answer, you can interrupt with
`tmux send-keys -t %17 C-c`.

## When you want a sub-agent in another pane

If the user wants a separate Claude conversation running alongside (a
"fork" of this session into a new pane), use the **`branch-pane`** skill,
not a manual split. It launches `claude --resume … --fork-session` so
the two transcripts don't fight.

`work-in-pane` is for shell work in panes. `branch-pane` is for spawning
another agent.

## Pane id stability — what to remember and what not to

- **`%<n>`** (pane id, e.g. `%17`) is stable for the whole life of that
  pane. Splitting, resizing, moving between windows — id stays. Safe to
  hold and to pass to scripts.
- **Pane index** (`0`, `1`, `2`) is what the human sees in the status
  line, but it can change when panes are split, killed, or reorganized.
  Use it when **talking to the user** ("pane 1"), not for storing or
  scripting.
- **Window index** and **session name** can also change. Don't store these.
- If the tmux server itself restarts, every `%<n>` is invalidated. Rare,
  but if a stored id suddenly returns "pane not found" from
  `tmux display-message`, treat that as the cause.

## Quick reference

| You want                              | Use                                       |
|---------------------------------------|-------------------------------------------|
| see what panes exist in this window   | `tmux list-panes -F …`                    |
| is this pane at a prompt?             | `tmux-is-idle <pane>`                     |
| run my command and wait for it        | `tmux-run <pane> "<cmd>"`                 |
| wait for someone else's command       | `tmux-wait <pane> [timeout]`              |
| split a new pane and get its id       | `tmux split-window -h -P -F '#{pane_id}'` |
| read what's on screen                 | `tmux-capture <pane> [lines]`             |
| interrupt a stuck pane                | `tmux send-keys -t <pane> C-c`            |
| fork this Claude session into a pane  | the `branch-pane` skill                   |

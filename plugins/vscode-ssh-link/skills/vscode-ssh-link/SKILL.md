---
name: vscode-ssh-link
description: >
  Enforces vscode:// links on every file path Claude mentions in prose, for
  the rest of the session once triggered — not just the one reply. Use on
  cues like "enable vscode links", "fix bare paths", "why isn't this a
  link", or a reply containing an unlinked file mention.
license: MIT
allowed-tools: Bash(*)
---

# vscode-ssh-link

## Resolved link settings (this session)

- url_prefix: !`if [ -n "$VSCODE_SSH_LINK_HOST" ]; then echo "vscode://vscode-remote/ssh-remote+$VSCODE_SSH_LINK_HOST/"; else echo "vscode://file/"; fi`
- label_prefix: !`echo "${VSCODE_SSH_LINK_LABEL_PREFIX:-}"`

Use these two values literally in every link below. Don't re-derive them, don't re-check the environment, don't ask the user for them.

## Referencing Code Locations

**MANDATORY — NO EXCEPTIONS.** Every mention of an existing file path MUST be rendered as a VS Code link. This is not optional, not "when convenient", and not "when the user asks". It applies in prose, code fences, lists, summaries, plans, end-of-turn recaps, questions to the user, and ANY path quoted from tool output (Read/Edit/Write/Glob/Grep), git status, or search results.

**Exception — files you write to disk.** Any `.md` or other file you author or edit (plan files, memory files, scratch notes, docs, READMEs, source files, config, etc.): the rule does NOT apply. Use plain backtick paths. `vscode://` links belong in chat replies only — inside files they just add noise and rot. The rule still applies the moment you quote those paths back to the user in chat.

**Files only — link exactly one concrete file.** Do NOT link directories (use a backtick path, e.g. `~/.claude/tmp/foo/`); the `:line:col` form is meaningless for them. Do NOT link glob/brace/sequence patterns (`src/**/*.ts`, `abcd{1-9}.txt`, `foo[0-9].log`) — they don't resolve to one file. Do NOT link files that don't exist yet (planned/proposed).

**Inline code / code fences — never link.** Paths inside backticks (`` `foo.cpp` ``, `` `foo.cpp:42` ``) or fenced code blocks stay as plain text. Backticks mean "verbatim token" — a `vscode://` link there would render as literal URL text, not a link. The rule applies only to path-shaped tokens in prose.

**Forbidden patterns — these are violations:**
- Bare file path: `src/components/Button.tsx`
- File-path-with-line in prose: `Button.tsx:42`
- Any file mention without the `vscode://...` link
- Wrapping a directory or glob pattern in the `vscode://...` link

**Before sending, scan your response.** Every path-shaped token for an existing file must be a link. If even one is bare, fix it before sending.

Format:

```
[<label_prefix><label>](<url_prefix><absolute_file_path>:<line>[:<col>])
```

- `<absolute_file_path>` — full path without leading `/`, URL-encoded per segment
- `:<line>` — **always required**. By default, use the earliest relevant line number (e.g. the first line of the function/block being referenced). If no specific line applies, use `1`.
- `:<col>` — optional column number
- `<label>` — relative path or filename or anything user-friendly
- `<label_prefix>` / `<url_prefix>` — the values from "Resolved link settings" above, verbatim

Examples (url_prefix `vscode://file/`, no label_prefix):

[src/main.cpp:42](vscode://file/home/alice/projects/demo/src/main.cpp:42)
[notes/draft #1.md](vscode://file/home/alice/projects/demo/notes/draft%20%231.md:1)

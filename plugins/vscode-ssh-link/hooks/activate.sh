#!/usr/bin/env bash
# SessionStart hook: emit vscode-link rule (from SKILL.md) as session context.
#
# Host resolution:
#   $VSCODE_SSH_LINK_HOST set   → ssh-remote form
#   $VSCODE_SSH_LINK_HOST unset → local vscode://file form

HOSTNAME="${VSCODE_SSH_LINK_HOST:-}"

if [ -n "$HOSTNAME" ]; then
  URL_PREFIX="vscode://vscode-remote/ssh-remote+${HOSTNAME}/"
else
  URL_PREFIX="vscode://file/"
fi

SKILL_PATH="${CLAUDE_PLUGIN_ROOT}/skills/vscode-ssh-link/SKILL.md"

echo "VSCODE-SSH-LINK MODE ACTIVE"
echo

# Strip the leading YAML frontmatter (first `---` … next `---`) and substitute
# the placeholder with the resolved URL prefix. Only the leading block is
# stripped — later `---` in the body (thematic breaks) are preserved.
sed -e '1{/^---$/!q; d}; 1,/^---$/d' "$SKILL_PATH" \
  | sed "s|__URL_PREFIX__|${URL_PREFIX}|g"

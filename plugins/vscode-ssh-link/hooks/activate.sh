#!/usr/bin/env bash
# SessionStart hook: emit vscode-link rule (from SKILL.md) as session context.
#
# Host resolution:
#   $VSCODE_SSH_LINK_HOST set   → ssh-remote form
#   $VSCODE_SSH_LINK_HOST unset → local vscode://file form
#
# label_prefix (optional):
#   $VSCODE_SSH_LINK_LABEL_PREFIX, e.g. "󰨞 " — prepended to every link label

HOSTNAME="${VSCODE_SSH_LINK_HOST:-}"
LABEL_PREFIX="${VSCODE_SSH_LINK_LABEL_PREFIX:-}"

if [ -n "$HOSTNAME" ]; then
  URL_PREFIX="vscode://vscode-remote/ssh-remote+${HOSTNAME}/"
else
  URL_PREFIX="vscode://file/"
fi

SKILL_PATH="${CLAUDE_PLUGIN_ROOT}/skills/vscode-ssh-link/SKILL.md"

echo "VSCODE-SSH-LINK MODE ACTIVE"
echo

# Strip the leading YAML frontmatter (first `---` … next `---`). Only the
# leading block is stripped — later `---` in the body (thematic breaks) are
# preserved. The "Resolved link settings" lines use `!`command`` dynamic
# context injection for standalone Skill invocation — SessionStart hook
# output doesn't go through that preprocessing, so replace those lines with
# the values resolved here instead.
sed -e '1{/^---$/!q; d}; 1,/^---$/d' "$SKILL_PATH" \
  | sed -e "s|^- url_prefix:.*|- url_prefix: ${URL_PREFIX}|" \
        -e "s|^- label_prefix:.*|- label_prefix: ${LABEL_PREFIX}|"

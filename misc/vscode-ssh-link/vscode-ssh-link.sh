#!/usr/bin/env bash
# PostToolUse hook: prints vscode:// link after file editing tools
# Usage: bash vscode-ssh-link.sh [hostname]
#   With hostname: generates ssh-remote link (vscode://vscode-remote/ssh-remote+HOST/...)
#   Without hostname: generates local link (vscode://file/...)

HOSTNAME="$1"

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // empty')
is_error=$(echo "$input" | jq -r '.tool_response.is_error // false')
if [ "$is_error" = "true" ]; then exit 0; fi
file_path=""

line_num=""

case "$tool_name" in
  Edit|Write)
    file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')
    line_num=$(echo "$input" | jq -r '.tool_response.structuredPatch[0].oldStart // 1')
    ;;
  NotebookEdit)
    file_path=$(echo "$input" | jq -r '.tool_input.notebook_path // empty')
    line_num="1"
    ;;
  Grep)
    file_path=$(echo "$input" | jq -r '.tool_input.path // empty')
    line_num="1"
    ;;
  Read)
    file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')
    line_num=$(echo "$input" | jq -r '.tool_response.file.startLine // 1')
    ;;
  *)
    exit 0
    ;;
esac

if [ -z "$file_path" ]; then
  exit 0
fi

# Percent-encode each path segment (preserving '/'), so terminal auto-link
# detection doesn't truncate at special characters like spaces, #, ?, &, etc.
encoded_path=$(printf '%s' "${file_path#/}" | jq -Rr 'split("/") | map(@uri) | join("/")')

# Always fall back to :1 — never emit a bare path. Without a line suffix,
# VS Code's remote handler can interpret the URI as a directory and open it
# in the explorer instead of as a file, which is jarring/unexpected.
line_num="${line_num:-1}"

if [ -n "$HOSTNAME" ]; then
  url="vscode://vscode-remote/ssh-remote+${HOSTNAME}/${encoded_path}:${line_num}"
else
  url="vscode://file/${encoded_path}:${line_num}"
fi

echo "{\"systemMessage\": \"${url}\"}"

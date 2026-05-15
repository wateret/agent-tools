#!/usr/bin/env bash
# PostToolUse hook: prints vscode:// remote link after file editing tools
# Usage: bash vscode-ssh-link.sh <hostname>

if [ -z "$1" ]; then
  echo "Error: hostname argument required" >&2
  exit 1
fi

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

# Strip leading slash for the URI path
uri_path="${file_path#/}"

# Percent-encode each path segment (preserving '/'), so terminal auto-link
# detection doesn't truncate at special characters like spaces, #, ?, &, etc.
uri_path=$(printf '%s' "$uri_path" | jq -Rr 'split("/") | map(@uri) | join("/")')

# Append line number (default to 1).
# Always fall back to :1 — never emit a bare path. Without a line suffix,
# VS Code's remote handler can interpret the URI as a directory and open it
# in the explorer instead of as a file, which is jarring/unexpected.
line_num="${line_num:-1}"
url="vscode://vscode-remote/ssh-remote+${HOSTNAME}/${uri_path}:${line_num}"

echo "{\"systemMessage\": \"${url}\"}"

#!/usr/bin/env bash
# Tmux status-right segment for Claude Code utilization.
# Shows today's utilization or work time with color coding and active session count.
# Usage: tmux-status.sh [--mode pct|time] [--theme dark|light] [--ttl SECONDS] [--sessions] [--turns]
# --sessions and --turns may be passed in any order; output order matches flag order.
# Results are cached for TTL seconds to avoid repeated computation.
MODE="time"
THEME="dark"
TTL=5
EXTRAS=()  # ordered list of extra parts: "sessions" / "turns"
while [[ $# -gt 0 ]]; do
  case "$1" in
  --mode)
    MODE="$2"
    shift 2
    ;;
  --theme)
    THEME="$2"
    shift 2
    ;;
  --ttl)
    TTL="$2"
    shift 2
    ;;
  --sessions)
    EXTRAS+=("sessions")
    shift
    ;;
  --turns)
    EXTRAS+=("turns")
    shift
    ;;
  *) shift ;;
  esac
done
EXTRAS_KEY=$(IFS=-; echo "${EXTRAS[*]}")
CACHE="/tmp/tmux-cc-yolo-status-${MODE}-${THEME}-${EXTRAS_KEY}"
YOLO_ARGS="--today"

# Return cached result if fresh enough
if [[ -f "$CACHE" ]]; then
  age=$(($(date +%s) - $(stat -c %Y "$CACHE" 2>/dev/null || echo 0)))
  if ((age < TTL)); then
    cat "$CACHE"
    exit 0
  fi
fi

s="$(dirname "$(readlink -f "$0")")/stats.py"
if [[ -f "$s" ]]; then
  json=$(python3 "$s" --json $YOLO_ARGS 2>/dev/null)
  if [[ -n "$json" ]]; then
    v=$(echo "$json" | jq -r '.utilization')
    work_ms=$(echo "$json" | jq -r '.work_ms')
    active=$(echo "$json" | jq -r '.active')
    turns=$(echo "$json" | jq -r '.turns')
    pct100=$(awk "BEGIN{printf \"%d\", $v*100}")
    if [[ "$THEME" == "light" ]]; then
      tier_top="#0077b6" tier_hi="#087f5b" tier_mid="#866800" tier_lo="#c05600" tier_bot="#c92a2a"
      active_color="#6741d9"
      dim_color="#868e96"
      err_color="#c92a2a"
    else
      tier_top="#8be9fd" tier_hi="#50fa7b" tier_mid="#f1fa8c" tier_lo="#ffb86c" tier_bot="#ff5555"
      active_color="#bd93f9"
      dim_color="#888888"
      err_color="#ff5555"
    fi
    # Time tier (utilization × 100): <12.5% / 12.5–24.99 / 25–49.99 / 50–99.99 / ≥100
    if ((pct100 >= 10000)); then color="$tier_top"
    elif ((pct100 >= 5000)); then color="$tier_hi"
    elif ((pct100 >= 2500)); then color="$tier_mid"
    elif ((pct100 >= 1250)); then color="$tier_lo"
    else color="$tier_bot"
    fi
    # Turns tier: <25 / 25–49 / 50–99 / 100–199 / ≥200
    if [[ "$turns" =~ ^[0-9]+$ ]]; then
      if ((turns >= 200)); then turns_color="$tier_top"
      elif ((turns >= 100)); then turns_color="$tier_hi"
      elif ((turns >= 50)); then turns_color="$tier_mid"
      elif ((turns >= 25)); then turns_color="$tier_lo"
      else turns_color="$tier_bot"
      fi
    else
      turns_color="$dim_color"
    fi
    if [[ "$MODE" == "time" ]]; then
      total_sec=$((work_ms / 1000))
      h=$((total_sec / 3600))
      m=$(((total_sec % 3600) / 60))
      s=$((total_sec % 60))
      if ((h > 0)); then
        vfmt=$(printf '%dh%02dm' "$h" "$m")
      else
        vfmt=$(printf '%dm%02ds' "$m" "$s")
      fi
    else
      if ((pct100 < 100)); then
        vfmt=$(printf '%.2f' "$v")
      else
        vfmt=$(printf '%#.3g' "$v" | sed 's/\.$//')
      fi
      vfmt="${vfmt}%"
    fi
    parts=()
    for kind in "${EXTRAS[@]}"; do
      case "$kind" in
      sessions)
        if [[ "$active" =~ ^[0-9]+$ ]] && ((active > 0)); then
          parts+=("#[fg=${active_color}]${active}󱐋")
        else
          parts+=("#[fg=${dim_color}]0󱐋")
        fi
        ;;
      turns)
        parts+=("#[fg=${turns_color}]${turns}󰭻")
        ;;
      esac
    done
    extra=""
    if ((${#parts[@]} > 0)); then
      IFS=' ' extra=" ${parts[*]}"
    fi
    result=$(printf '#[fg=%s]🔥 %s%s#[default]' "$color" "$vfmt" "$extra")
  else
    result=$(printf '#[fg=%s]🔥 ERROR#[default]' "${err_color:-#ff5555}")
  fi
else
  result=$(printf '#[fg=%s]🔥 ERROR#[default]' "${err_color:-#ff5555}")
fi

printf '%s' "$result" >"$CACHE"
printf '%s' "$result"

#!/bin/bash
# Flash a Home Assistant light
# Usage: flash.sh [color]  — color is "green" (default) or "red"
#   green: 3 flashes then restore
#   red:   flash indefinitely until killed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"
PIDFILE="$SCRIPT_DIR/.flash.pid"

COLOR="${1:-green}"
case "$COLOR" in
  red)   RGB="[255,0,0]" ;;
  *)     RGB="[0,255,0]" ;;
esac

AUTH="Authorization: Bearer $HA_TOKEN"
CONTENT="Content-Type: application/json"
API="$HA_URL/api/services/light"

# Save current light state
STATE=$(curl -s -H "$AUTH" "$HA_URL/api/states/$HA_ENTITY")
WAS_ON=$(echo "$STATE" | grep -o '"state":"on"' | head -1)
PREV_BRIGHTNESS=$(echo "$STATE" | sed -n 's/.*"brightness":\s*\([0-9]*\).*/\1/p' | head -1)
PREV_RGB=$(echo "$STATE" | sed -n 's/.*"rgb_color":\s*\(\[[0-9, ]*\]\).*/\1/p' | head -1)

# Save restore info so stop script can use it
echo "$WAS_ON" > "$SCRIPT_DIR/.flash_prev_on"
echo "$PREV_BRIGHTNESS" > "$SCRIPT_DIR/.flash_prev_brightness"
echo "$PREV_RGB" > "$SCRIPT_DIR/.flash_prev_rgb"

flash_on() {
  curl -s -o /dev/null -H "$AUTH" -H "$CONTENT" \
    -d "{\"entity_id\":\"$HA_ENTITY\",\"rgb_color\":$RGB,\"brightness\":255}" \
    "$API/turn_on"
}

flash_off() {
  curl -s -o /dev/null -H "$AUTH" -H "$CONTENT" \
    -d "{\"entity_id\":\"$HA_ENTITY\"}" \
    "$API/turn_off"
}

# Always kill any existing red flash loop first
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null
  rm -f "$PIDFILE"
fi

if [ "$COLOR" = "red" ]; then
  echo $$ > "$PIDFILE"

  # Flash red indefinitely until killed
  while true; do
    flash_on
    sleep 0.4
    flash_off
    sleep 0.3
  done
else
  # 3 green flashes then restore
  for i in 1 2 3; do
    flash_on
    sleep 0.4
    flash_off
    sleep 0.3
  done

  # Restore previous state
  if [ -n "$WAS_ON" ]; then
    RESTORE="{\"entity_id\":\"$HA_ENTITY\""
    [ -n "$PREV_BRIGHTNESS" ] && RESTORE="$RESTORE,\"brightness\":$PREV_BRIGHTNESS"
    [ -n "$PREV_RGB" ] && RESTORE="$RESTORE,\"rgb_color\":$PREV_RGB"
    RESTORE="$RESTORE}"
    curl -s -o /dev/null -H "$AUTH" -H "$CONTENT" -d "$RESTORE" "$API/turn_on"
  fi
fi

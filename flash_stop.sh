#!/bin/bash
# Stop the red flash loop and restore the light

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"
PIDFILE="$SCRIPT_DIR/.flash.pid"

AUTH="Authorization: Bearer $HA_TOKEN"
CONTENT="Content-Type: application/json"
API="$HA_URL/api/services/light"

# Kill the flash loop if running
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null
  rm -f "$PIDFILE"
fi

# Restore previous state
WAS_ON=$(cat "$SCRIPT_DIR/.flash_prev_on" 2>/dev/null)
PREV_BRIGHTNESS=$(cat "$SCRIPT_DIR/.flash_prev_brightness" 2>/dev/null)
PREV_RGB=$(cat "$SCRIPT_DIR/.flash_prev_rgb" 2>/dev/null)

if [ -n "$WAS_ON" ]; then
  RESTORE="{\"entity_id\":\"$HA_ENTITY\""
  [ -n "$PREV_BRIGHTNESS" ] && RESTORE="$RESTORE,\"brightness\":$PREV_BRIGHTNESS"
  [ -n "$PREV_RGB" ] && RESTORE="$RESTORE,\"rgb_color\":$PREV_RGB"
  RESTORE="$RESTORE}"
  curl -s -o /dev/null -H "$AUTH" -H "$CONTENT" -d "$RESTORE" "$API/turn_on"
else
  curl -s -o /dev/null -H "$AUTH" -H "$CONTENT" \
    -d "{\"entity_id\":\"$HA_ENTITY\"}" \
    "$API/turn_off"
fi

rm -f "$SCRIPT_DIR/.flash_prev_on" "$SCRIPT_DIR/.flash_prev_brightness" "$SCRIPT_DIR/.flash_prev_rgb"

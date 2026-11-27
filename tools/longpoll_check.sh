#!/bin/sh
# Check a 2v2 long-poll survives the proxy in front of the API: makes a throwaway room, holds
# GET /room?v=N (the server waits up to 25 s), prints how long it was held, then leaves the room.
#   tools/longpoll_check.sh http://127.0.0.1:8197/api                       # preview proxy
#   tools/longpoll_check.sh https://riccioserver.tail0d3eb6.ts.net:10000/api PUBLIC   # via Funnel
# PUBLIC resolves the host through 1.1.1.1 so the request really goes through Funnel, not the tailnet.
# Expect "held 25 s". Much less (or an error) means the proxy cut it: drop live.HOLD to 15.
API="$1"; [ -n "$API" ] || { echo "usage: $0 API_BASE [PUBLIC]" >&2; exit 2; }
RES=""
if [ "$2" = PUBLIC ]; then
  HOSTPORT=$(echo "$API" | sed -E 's#^https?://([^/]+).*#\1#')
  HOST=${HOSTPORT%:*}; PORT=${HOSTPORT##*:}
  IP=$(dig +short @1.1.1.1 "$HOST" | tail -1)
  RES="--resolve $HOST:$PORT:$IP"
fi
PID=$(cat /proc/sys/kernel/random/uuid)
NAME="Poll$(date +%S%M)"
J='Content-Type: application/json'
CODE=$(curl -fsS $RES -H "$J" -d "{\"name\":\"$NAME\",\"player_id\":\"$PID\"}" "$API/live/room/create" \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])') || { echo "create failed"; exit 1; }
V=$(curl -fsS $RES "$API/live/room?code=$CODE&player_id=$PID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["v"])')
T0=$(date +%s)
curl -fsS $RES --max-time 60 -o /dev/null "$API/live/room?code=$CODE&player_id=$PID&v=$V"; RC=$?
echo "room $CODE: long-poll held $(( $(date +%s) - T0 )) s (curl exit $RC)"
curl -fsS $RES -o /dev/null -H "$J" -d "{\"player_id\":\"$PID\",\"code\":\"$CODE\"}" "$API/live/room/leave"

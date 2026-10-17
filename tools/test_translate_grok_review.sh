#!/bin/sh
set -eu

skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
wrapper="$(dirname -- "$0")/translate_grok_review.sh"
tmpdir=$(mktemp -d "${TMPDIR:-/private/tmp}/grok-opinion-test.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM

mock="$tmpdir/grok"
mock_scutil="$tmpdir/scutil"
cat >"$mock" <<'EOF'
#!/bin/sh
if [ -n "${EXPECT_HTTPS_PROXY:-}" ]; then
  [ "${HTTPS_PROXY:-}" = "$EXPECT_HTTPS_PROXY" ] || exit 72
  [ "${https_proxy:-}" = "$EXPECT_HTTPS_PROXY" ] || exit 73
fi
for argument in "$@"; do
  [ "$argument" != "-p" ] || exit 70
done
if [ "${MOCK_GROK_MODE:-success}" = "failure" ]; then
  echo "No auth credentials for cli-chat-proxy" >&2
  exit 41
fi
printf '%s\n' '{"text":"review complete","thought":"must not escape","stopReason":"EndTurn","sessionId":"test-session","requestId":"test-request"}'
EOF
chmod 700 "$mock"

cat >"$mock_scutil" <<'EOF'
#!/bin/sh
cat <<'PROXY'
<dictionary> {
  HTTPSEnable : 1
  HTTPSPort : 6152
  HTTPSProxy : 127.0.0.1
}
PROXY
EOF
chmod 700 "$mock_scutil"

success=$(GROK_BIN="$mock" sh "$wrapper" "redacted test packet")
python3 - "$success" <<'PY'
import json
import sys

assert json.loads(sys.argv[1]) == {
    "status": "ok",
    "text": "review complete",
    "stopReason": "EndTurn",
    "sessionId": "test-session",
    "requestId": "test-request",
}
PY

proxy_success=$(env -u HTTPS_PROXY -u https_proxy GROK_BIN="$mock" SCUTIL_BIN="$mock_scutil" EXPECT_HTTPS_PROXY="http://127.0.0.1:6152" sh "$wrapper" "redacted proxy test packet")
python3 - "$proxy_success" <<'PY'
import json
import sys

assert json.loads(sys.argv[1])["status"] == "ok"
PY

set +e
failure=$(GROK_BIN="$mock" MOCK_GROK_MODE=failure sh "$wrapper" "redacted test packet")
status=$?
set -e
[ "$status" -eq 41 ]
python3 - "$failure" <<'PY'
import json
import sys

assert json.loads(sys.argv[1]) == {
    "status": "error",
    "category": "authentication",
    "exitCode": 41,
}
PY

set +e
invalid=$(GROK_BIN="$mock" sh "$wrapper" "xai-12345678901234567890")
status=$?
set -e
[ "$status" -eq 2 ]
python3 - "$invalid" <<'PY'
import json
import sys

assert json.loads(sys.argv[1]) == {
    "status": "error",
    "category": "invalid-packet",
    "exitCode": 2,
}
PY

echo "grok-opinion wrapper: OK"

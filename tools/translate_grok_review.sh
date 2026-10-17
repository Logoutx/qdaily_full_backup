#!/bin/sh
set -eu

emit_error() {
  printf '{"status":"error","category":"%s","exitCode":%s}\n' "$1" "$2"
}

if [ "$#" -ne 1 ]; then
  echo "Usage: grok-opinion.sh <review-packet>" >&2
  exit 2
fi

GROK_BIN="${GROK_BIN:-$HOME/.local/bin/grok}"
if [ ! -x "$GROK_BIN" ]; then
  emit_error "missing-cli" 1
  exit 1
fi

# Command-line clients do not automatically inherit macOS System Proxy
# settings. When no explicit HTTPS proxy is present, mirror the active
# system HTTPS proxy for this Grok process only. This does not change any
# system network setting and remains overrideable through the environment.
if [ -z "${HTTPS_PROXY:-}" ] && [ -z "${https_proxy:-}" ]; then
  scutil_bin="${SCUTIL_BIN:-}"
  if [ -z "$scutil_bin" ]; then
    scutil_bin=$(command -v scutil 2>/dev/null || true)
  fi
  if [ -n "$scutil_bin" ] && [ -x "$scutil_bin" ]; then
    proxy_config=$("$scutil_bin" --proxy 2>/dev/null || true)
    proxy_enabled=$(printf '%s\n' "$proxy_config" | sed -n 's/^[[:space:]]*HTTPSEnable[[:space:]]*:[[:space:]]*//p' | head -n 1)
    proxy_host=$(printf '%s\n' "$proxy_config" | sed -n 's/^[[:space:]]*HTTPSProxy[[:space:]]*:[[:space:]]*//p' | head -n 1)
    proxy_port=$(printf '%s\n' "$proxy_config" | sed -n 's/^[[:space:]]*HTTPSPort[[:space:]]*:[[:space:]]*//p' | head -n 1)
    case "$proxy_port" in
      ''|*[!0-9]*) proxy_port= ;;
    esac
    if [ "$proxy_enabled" = "1" ] && [ -n "$proxy_host" ] && [ -n "$proxy_port" ]; then
      HTTPS_PROXY="http://$proxy_host:$proxy_port"
      https_proxy="$HTTPS_PROXY"
      export HTTPS_PROXY https_proxy
    fi
  fi
fi

base_tmp=/private/tmp
[ -d "$base_tmp" ] || base_tmp=/tmp
if ! work_dir=$(mktemp -d "$base_tmp/grok-opinion.XXXXXX" 2>/dev/null); then
  emit_error "temporary-directory" 1
  exit 1
fi
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
cd "$work_dir"

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  emit_error "unsafe-working-directory" 1
  exit 1
fi

# Public archive source + complete English translation can exceed the sidecar's
# default short-packet limit. Keep a finite whole-article bound and identical isolation.
packet=$1
if [ "${#packet}" -gt 160000 ]; then
  emit_error "packet-too-large" 2
  exit 2
fi
if printf '%s' "$packet" | grep -Eiq 'xai-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{16,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|Authorization:[[:space:]]*Bearer|https?://[^[:space:]@]+@'; then
  emit_error "invalid-packet" 2
  exit 2
fi

umask 077
prompt_file="$work_dir/review-prompt.txt"
result_file="$work_dir/result.json"
error_file="$work_dir/error.txt"
# Let the official client perform normal refresh before entering strict mode,
# which intentionally cannot write auth.json.lock. This command lists models;
# it does not generate content, change grants, or use a separate API credential.
if "$GROK_BIN" models > /dev/null 2>"$error_file"; then
  :
else
  preflight_status=$?
  emit_error "authentication" "$preflight_status"
  exit "$preflight_status"
fi
printf '%s\n\n%s\n%s\n' \
  "Act as an independent, read-only reviewer. Assess only the review packet below. Do not modify files, run shell commands, use browser, web search, MCP, connected apps, subagents, memory, or external tools. Do not reveal credentials. State assumptions, uncertainty, and highest-impact flaws first." \
  "REVIEW PACKET:" \
  "$packet" >"$prompt_file"

set -- --no-auto-update --prompt-file "$prompt_file" --output-format json --max-turns 1 \
  --permission-mode dontAsk --sandbox strict --no-subagents --no-memory --disable-web-search \
  --deny 'Bash(*)' --deny 'Edit(*)' --deny 'MCPTool(*)' --deny 'WebFetch(*)'

if [ -n "${GROK_MODEL:-}" ]; then
  if "$GROK_BIN" "$@" --model "$GROK_MODEL" >"$result_file" 2>"$error_file"; then run_status=0; else run_status=$?; fi
else
  if "$GROK_BIN" "$@" >"$result_file" 2>"$error_file"; then run_status=0; else run_status=$?; fi
fi

if [ "$run_status" -ne 0 ]; then
  if grep -Eiq 'auth|login|credential' "$error_file"; then category=authentication
  elif grep -Eiq 'network|dns|connect|timeout' "$error_file"; then category=network
  else category=invocation
  fi
  emit_error "$category" "$run_status"
  exit "$run_status"
fi

if [ "$(wc -c <"$result_file")" -gt 200000 ]; then
  emit_error "response-too-large" 1
  exit 1
fi

python3 - "$result_file" <<'PY'
import json
import sys

try:
    with open(sys.argv[1]) as handle:
        response = json.load(handle)
except (OSError, json.JSONDecodeError):
    print(json.dumps({"status": "error", "category": "invalid-response", "exitCode": 1}))
    sys.exit(1)

output = {"status": "ok"}
output.update({
    key: response.get(key)
    for key in ("text", "stopReason", "sessionId", "requestId")
    if key in response
})
print(json.dumps(output, ensure_ascii=False))
PY

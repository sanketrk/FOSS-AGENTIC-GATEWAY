#!/bin/sh
set -eu

: "${GATEWAY_URL:?Set GATEWAY_URL, for example https://mcp.example.com}"
: "${ACCESS_TOKEN:?Set ACCESS_TOKEN to a valid OAuth JWT access token}"

mcp_path="${MCP_PATH:-/mcp}"
endpoint="${GATEWAY_URL%/}$mcp_path"
resource_url="${RESOURCE_URL:-${GATEWAY_URL%/}$mcp_path}"
resource_metadata_url="${RESOURCE_METADATA_URL:-${GATEWAY_URL%/}/.well-known/oauth-protected-resource$mcp_path}"
tmpdir=$(mktemp -d)
trap 'rm -f "$tmpdir/body" "$tmpdir/headers"; rmdir "$tmpdir"' EXIT HUP INT TERM

body='{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"mcp-gateway-smoke","version":"1.0.0"}}}}'
legacy_initialize='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"mcp-gateway-smoke-legacy","version":"1.0.0"}}}'
accept='application/json, text/event-stream'

request_status() {
  curl --silent --show-error --output "$tmpdir/body" --dump-header "$tmpdir/headers" --write-out '%{http_code}' "$@"
}

assert_status() {
  name=$1
  expected=$2
  shift 2
  actual=$(request_status "$@")
  if [ "$actual" != "$expected" ]; then
    printf '%s: expected HTTP %s, got %s\n' "$name" "$expected" "$actual" >&2
    cat "$tmpdir/body" >&2
    exit 1
  fi
}

assert_status 'legacy GET stream is protected by OAuth' 401 \
  --request GET "$endpoint" \
  --header 'Accept: text/event-stream' \
  --header 'MCP-Protocol-Version: 2025-11-25' \
  --header 'Mcp-Session-Id: legacy-session'

assert_status 'legacy DELETE is protected by OAuth' 401 \
  --request DELETE "$endpoint" \
  --header 'MCP-Protocol-Version: 2025-11-25' \
  --header 'Mcp-Session-Id: legacy-session'

assert_status 'legacy initialize reaches OAuth validation' 401 \
  --request POST "$endpoint" \
  --header 'Content-Type: application/json' \
  --header "Accept: $accept" \
  --data "$legacy_initialize"

assert_status 'path-specific protected-resource metadata is public' 200 \
  --request GET "$resource_metadata_url"
if ! grep -Fq "\"resource\":\"$resource_url\"" "$tmpdir/body" \
  || ! grep -q '"authorization_servers":' "$tmpdir/body"; then
  printf 'Protected-resource metadata is missing its resource or authorization server list.\n' >&2
  cat "$tmpdir/body" >&2
  exit 1
fi

if [ "$mcp_path" = /mcp ]; then
  assert_status 'root protected-resource metadata is public' 200 \
    --request GET "${GATEWAY_URL%/}/.well-known/oauth-protected-resource"
fi

assert_status 'protocol header/body mismatch is rejected' 400 \
  --request POST "$endpoint" \
  --header 'Content-Type: application/json' \
  --header "Accept: $accept" \
  --header 'MCP-Protocol-Version: 2025-11-25' \
  --header 'Mcp-Method: server/discover' \
  --data "$body"
if ! grep -q '"code"[[:space:]]*:[[:space:]]*-32020' "$tmpdir/body"; then
  printf 'Expected a JSON-RPC HeaderMismatch (-32020) response.\n' >&2
  cat "$tmpdir/body" >&2
  exit 1
fi

assert_status 'missing Mcp-Method is rejected' 400 \
  --request POST "$endpoint" \
  --header 'Content-Type: application/json' \
  --header "Accept: $accept" \
  --header 'MCP-Protocol-Version: 2026-07-28' \
  --data "$body"

assert_status 'valid unauthenticated request reaches authentication' 401 \
  --request POST "$endpoint" \
  --header 'Content-Type: application/json' \
  --header "Accept: $accept" \
  --header 'MCP-Protocol-Version: 2026-07-28' \
  --header 'Mcp-Method: server/discover' \
  --data "$body"
if ! grep -Fqi "resource_metadata=\"$resource_metadata_url\"" "$tmpdir/headers"; then
  printf '401 response did not advertise protected-resource metadata discovery.\n' >&2
  cat "$tmpdir/headers" >&2
  exit 1
fi

assert_status 'authenticated current-spec discover request is proxied' 200 \
  --request POST "$endpoint" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --header 'Content-Type: application/json' \
  --header "Accept: $accept" \
  --header 'MCP-Protocol-Version: 2026-07-28' \
  --header 'Mcp-Method: server/discover' \
  --header 'Mcp-Session-Id: legacy-session' \
  --header 'Last-Event-ID: legacy-event' \
  --data "$body"

content_type=$(grep -i '^content-type:' "$tmpdir/headers" | tail -n 1 | cut -d: -f2- | tr -d '\r' | tr '[:upper:]' '[:lower:]')
case "$content_type" in
  *application/json*|*text/event-stream*)
    ;;
  *)
    printf 'Expected a JSON or SSE MCP response, got Content-Type: %s\n' "$content_type" >&2
    exit 1
    ;;
esac

if ! grep -Eq '"jsonrpc"[[:space:]]*:[[:space:]]*"2\.0"|^data:' "$tmpdir/body"; then
  printf 'Authenticated server/discover response did not contain a JSON-RPC message.\n' >&2
  cat "$tmpdir/body" >&2
  exit 1
fi

printf 'Current-spec transport and authenticated server/discover smoke tests passed.\n'

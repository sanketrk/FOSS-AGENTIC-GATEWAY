package.path = "/usr/local/openresty/lualib/?.lua;/usr/local/openresty/lualib/?/init.lua;"
  .. "/usr/local/share/lua/5.1/?.lua;/usr/local/share/lua/5.1/?/init.lua;" .. package.path
package.cpath = "/usr/local/openresty/lualib/?.so;" .. package.cpath

local cjson = require "cjson.safe"
local current_claims = { iss = "https://issuer.example.com/", aud = "mcp-gateway", scope = "mcp:access" }
local test_token_issuer = "issuer11"
local token_verifications = 0
local verified_discovery
local last_decoded_value
local decoded_values = {
  ["5LiW55WM"] = "世界",
  ["issuer11"] = '{"iss":"https://issuer.example.com/"}',
  ["issuer22"] = '{"iss":"https://second-idp.example.com/"}',
  ["issuer33"] = '{"iss":"https://unconfigured-idp.example.com/"}',
}

package.preload["resty.openidc"] = function()
  return {
    bearer_jwt_verify = function(opts)
      token_verifications = token_verifications + 1
      verified_discovery = opts.discovery
      return current_claims
    end,
  }
end

ngx = {
  decode_base64 = function(value)
    last_decoded_value = value
    return decoded_values[value] or decoded_values[value:gsub("=", "")]
  end,
}

local state
kong = {
  request = {
    get_method = function()
      return state.method
    end,
    get_path = function()
      return state.path or "/mcp"
    end,
    get_header = function(name)
      return state.headers[name:lower()]
    end,
    get_raw_body = function()
      return state.body
    end,
  },
  ctx = { shared = {} },
  response = {
    exit = function(status, body, headers)
      state.response = { status = status, body = body, headers = headers }
      return state.response
    end,
    clear_header = function(name)
      state.response_headers_cleared = state.response_headers_cleared or {}
      state.response_headers_cleared[name:lower()] = true
    end,
  },
  service = {
    request = {
      clear_header = function(name)
        state.cleared[name:lower()] = true
      end,
    },
  },
  log = {
    info = function() end,
    warn = function() end,
  },
}

local gateway = require "kong.plugins.mcp-gateway.handler"
local config = {
  resource_url = "https://mcp.example.com/mcp",
  resource_metadata_url = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
  metadata_paths = {
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
  },
  authorization_servers = {
    {
      issuer = "https://issuer.example.com/",
      discovery_url = "https://issuer.example.com/.well-known/openid-configuration",
    },
  },
  audience = "mcp-gateway",
  scopes_supported = { "mcp:access" },
  signing_algorithms = { "RS256" },
  ssl_verify = true,
  allowed_origins = { "https://client.example.com" },
  required_scopes = { "mcp:access" },
  forward_bearer_token = false,
  legacy_protocol_versions = { "2025-11-25", "2025-03-26" },
}

local function make_message(method, params, request_id)
  params = params or {}
  if request_id ~= false then
    params._meta = params._meta or {
      ["io.modelcontextprotocol/protocolVersion"] = "2026-07-28",
      ["io.modelcontextprotocol/clientCapabilities"] = {},
      ["io.modelcontextprotocol/clientInfo"] = { name = "gateway-test", version = "1.0" },
    }
  end
  local message = { jsonrpc = "2.0", method = method, params = params }
  if request_id ~= false then
    message.id = request_id or 1
  end
  return cjson.encode(message)
end

local function request(overrides)
  overrides = overrides or {}
  local body = make_message("server/discover", {}, 1)
  state = {
    method = "POST",
    headers = {
      ["content-type"] = "application/json",
      accept = "application/json, text/event-stream",
      ["mcp-protocol-version"] = "2026-07-28",
      ["mcp-method"] = "server/discover",
      authorization = "Bearer a.issuer-one.c",
    },
    body = body,
    cleared = {},
  }
  kong.ctx.shared = {}
  for key, value in pairs(overrides.headers or {}) do
    state.headers[key:lower()] = value ~= false and value or nil
  end
  if state.headers.authorization then
    local bearer_scheme, bearer_separator = "Bearer", " "
    state.headers.authorization = bearer_scheme .. bearer_separator .. "header."
      .. test_token_issuer .. ".signature"
  end
  if state.headers.authorization then
    state.headers.authorization = "Bearer header." .. test_token_issuer .. ".signature"
  end
  local authorization_override = overrides.headers and overrides.headers.authorization
  if authorization_override == false then
    state.headers.authorization = nil
  else
    local bearer_scheme, bearer_separator = "Bearer", " "
    state.headers.authorization = bearer_scheme .. bearer_separator .. "header."
      .. test_token_issuer .. ".signature"
  end
  if overrides.method then
    state.method = overrides.method
  end
  if overrides.body then
    state.body = overrides.body
  end
  return gateway:access(config)
end

local function expect_status(name, expected_status, overrides, expected_code)
  local result = request(overrides)
  assert(state.response, name .. ": expected a response")
  assert(state.response.status == expected_status,
    name .. ": expected status " .. expected_status .. ", got " .. state.response.status)
  if expected_code then
    assert(state.response.body.error.code == expected_code, name .. ": unexpected JSON-RPC error code")
  end
  return result
end

local passed = 0
local function test(name, callback)
  callback()
  passed = passed + 1
  io.write("ok ", passed, " - ", name, "\n")
end

test("valid current-version POST reaches bearer authentication", function()
  expect_status("valid POST", 401, { headers = { authorization = false } })
  assert(token_verifications == 0, "missing bearer token must not be verified")
  assert(state.cleared["mcp-session-id"] and state.cleared["last-event-id"],
    "legacy session and resume headers must be removed")
  assert(state.response.headers["WWW-Authenticate"]:find(
    'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"', 1, true),
    "unauthorized response must advertise the protected resource metadata URL")
  assert(state.response.headers["WWW-Authenticate"]:find('scope="mcp:access"', 1, true),
    "unauthorized response must advertise required scopes")
end)

test("valid JWT reaches upstream and is stripped by default", function()
  request({ headers = { authorization = "test-token" } })
  assert(not state.response, "valid request should continue to the upstream; got "
    .. tostring(state.response and state.response.status) .. " "
    .. tostring(state.response and cjson.encode(state.response.body))
    .. "; verified=" .. token_verifications
    .. "; discovery=" .. tostring(verified_discovery)
    .. "; decoded=" .. tostring(last_decoded_value))
  assert(state.cleared.authorization, "bearer token should not be forwarded by default")
  assert(verified_discovery == "https://issuer.example.com/.well-known/openid-configuration",
    "JWT verifier must use the configured issuer discovery URL")
end)

test("protected resource metadata is public and advertises all authorization servers", function()
  config.authorization_servers[2] = {
    issuer = "https://second-idp.example.com/",
    discovery_url = "https://second-idp.example.com/.well-known/openid-configuration",
  }
  state = {
    method = "GET",
    path = "/.well-known/oauth-protected-resource/mcp",
    headers = { origin = "https://unlisted-browser.example" },
    cleared = {},
  }
  gateway:access(config)
  assert(state.response and state.response.status == 200, "metadata endpoint must be public")
  local document = cjson.decode(state.response.body)
  assert(document.resource == config.resource_url, "metadata must identify the protected MCP resource")
  assert(#document.authorization_servers == 2, "metadata must advertise all configured IdPs")
  assert(document.authorization_servers[2] == "https://second-idp.example.com/",
    "metadata must advertise the configured second issuer")
  assert(document.bearer_methods_supported[1] == "header", "metadata must specify bearer header transport")
  assert(state.response.headers["Content-Type"] == "application/json", "metadata must be JSON")
  config.authorization_servers[2] = nil
end)

test("a token from each advertised issuer uses only its configured discovery URL", function()
  config.authorization_servers[2] = {
    issuer = "https://second-idp.example.com/",
    discovery_url = "https://second-idp.example.com/.well-known/openid-configuration",
  }
  current_claims = { iss = "https://second-idp.example.com/", aud = "mcp-gateway", scope = "mcp:access" }
  test_token_issuer = "issuer22"
  request({ headers = { authorization = "Bearer a.issuer-two.c" } })
  assert(not state.response, "token from second configured IdP should be accepted")
  assert(verified_discovery == "https://second-idp.example.com/.well-known/openid-configuration",
    "issuer must select its matching configured discovery URL")
  config.authorization_servers[2] = nil
  test_token_issuer = "issuer11"
  current_claims = { iss = "https://issuer.example.com/", aud = "mcp-gateway", scope = "mcp:access" }
end)

test("unknown JWT issuers are rejected without contacting an unconfigured issuer", function()
  local verification_count = token_verifications
  test_token_issuer = "issuer33"
  request({ headers = { authorization = "Bearer a.unknown-issuer.c" } })
  assert(state.response and state.response.status == 401, "unconfigured issuer must be rejected")
  test_token_issuer = "issuer11"
  assert(token_verifications == verification_count, "untrusted token issuer must not drive discovery")
  assert(state.response.headers["WWW-Authenticate"]:find("resource_metadata=", 1, true),
    "invalid-token challenge must still advertise the metadata URL")
end)

test("notification POSTs do not require version metadata", function()
  request({
    body = make_message("notifications/message", {}, false),
    headers = {
      ["mcp-method"] = "notifications/message",
      ["mcp-protocol-version"] = "2026-07-28",
      authorization = false,
    },
  })
  assert(state.response and state.response.status == 401,
    "notification must pass transport validation and reach authentication")
end)

test("legacy GET and DELETE transports reach authentication", function()
  expect_status("GET", 401, {
    method = "GET",
    headers = {
      accept = "text/event-stream",
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-session-id"] = "legacy-session",
      authorization = false,
    },
  })
  expect_status("DELETE", 401, {
    method = "DELETE",
    headers = {
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-session-id"] = "legacy-session",
      authorization = false,
    },
  })
end)

test("current-version GET and DELETE are rejected", function()
  expect_status("current GET", 405, {
    method = "GET",
    headers = {
      accept = "text/event-stream",
      ["mcp-protocol-version"] = "2026-07-28",
      ["mcp-session-id"] = "legacy-session",
    },
  })
  expect_status("current DELETE", 405, {
    method = "DELETE",
    headers = { ["mcp-protocol-version"] = "2026-07-28", ["mcp-session-id"] = "legacy-session" },
  })
end)

test("non-JSON POST is rejected", function()
  expect_status("content type", 415, { headers = { ["content-type"] = "text/plain" } })
end)

test("POST must accept JSON and SSE", function()
  expect_status("missing SSE accept", 406, { headers = { accept = "application/json" } })
  expect_status("zero-quality SSE accept", 406, {
    headers = { accept = "application/json, text/event-stream;q=0" },
  })
  expect_status("invalid-quality SSE accept", 406, {
    headers = { accept = "application/json, text/event-stream;q=invalid" },
  })
end)

test("JSON-RPC method header is required and matched", function()
  expect_status("missing method header", 400, { headers = { ["mcp-method"] = false } }, -32020)
  expect_status("mismatched method header", 400, { headers = { ["mcp-method"] = "tools/list" } }, -32020)
end)

test("protocol version header matches request metadata", function()
  expect_status("missing version header", 400, { headers = { ["mcp-protocol-version"] = false } }, -32020)
  expect_status("mismatched version header", 400, {
    headers = { ["mcp-protocol-version"] = "2025-11-25" },
  }, -32020)
  local missing_capabilities = cjson.encode({
    jsonrpc = "2.0",
    id = 11,
    method = "server/discover",
    params = { _meta = { ["io.modelcontextprotocol/protocolVersion"] = "2026-07-28" } },
  })
  expect_status("missing client capabilities", 400, { body = missing_capabilities }, -32020)
end)

test("tool, resource, and prompt requests require matching Mcp-Name", function()
  local tool_body = make_message("tools/call", { name = "lookup" }, 7)
  expect_status("missing tool name", 400, {
    body = tool_body,
    headers = { ["mcp-method"] = "tools/call", ["mcp-name"] = false },
  }, -32020)
  expect_status("mismatched tool name", 400, {
    body = tool_body,
    headers = { ["mcp-method"] = "tools/call", ["mcp-name"] = "other" },
  }, -32020)

  local read_body = make_message("resources/read", { uri = "file:///tmp/a" }, 8)
  request({
    body = read_body,
    headers = { ["mcp-method"] = "resources/read", ["mcp-name"] = "file:///tmp/a" },
  })
  assert(not state.response, "matching resource URI should be accepted")

  local prompt_body = make_message("prompts/get", { name = "review" }, 9)
  request({
    body = prompt_body,
    headers = { ["mcp-method"] = "prompts/get", ["mcp-name"] = "review" },
  })
  assert(not state.response, "matching prompt name should be accepted")
end)

test("Mcp-Name decodes the specified Base64 sentinel", function()
  local body = make_message("tools/call", { name = "世界" }, 10)
  request({
    body = body,
    headers = {
      ["mcp-method"] = "tools/call",
      ["mcp-name"] = "=?base64?5LiW55WM?=",
    },
  })
  assert(not state.response, "valid encoded tool name should be accepted")
end)

test("extension methods are passed through without standard Mcp-Name requirements", function()
  local body = make_message("vendor/custom", { name = "tenant-1" }, 12)
  request({
    body = body,
    headers = { ["mcp-method"] = "vendor/custom" },
  })
  assert(not state.response, "extension methods should remain proxyable")
end)

test("malformed and mismatched JSON-RPC bodies are rejected", function()
  expect_status("malformed JSON", 400, { body = "{" })
  expect_status("mismatched JSON-RPC version", 400, {
    body = '{"jsonrpc":"1.0","id":2,"method":"server/discover"}',
  })
  expect_status("JSON-RPC response sent to server", 400, {
    body = '{"jsonrpc":"2.0","id":2,"result":{}}',
  })
end)

test("Origin allow-list is enforced", function()
  expect_status("untrusted Origin", 403, {
    headers = { origin = "https://attacker.example" },
  })
end)

test("legacy session and resume headers are stripped on current-version requests", function()
  request({ headers = { ["mcp-session-id"] = "old-session", ["last-event-id"] = "old-event" } })
  assert(not state.response, "legacy headers must not prevent a modern POST")
  assert(state.cleared["mcp-session-id"] and state.cleared["last-event-id"])
  gateway:header_filter(config)
  assert(state.response_headers_cleared["mcp-session-id"], "legacy session response header must be stripped")
end)

test("legacy initialization negotiates configured protocol version", function()
  local initialize = cjson.encode({
    jsonrpc = "2.0",
    id = 1,
    method = "initialize",
    params = {
      protocolVersion = "2025-11-25",
      capabilities = {},
      clientInfo = { name = "legacy-test", version = "1.0" },
    },
  })
  request({
    body = initialize,
    headers = {
      ["mcp-protocol-version"] = false,
      ["mcp-method"] = false,
      ["mcp-name"] = false,
    },
  })
  assert(not state.response, "supported legacy initialize request should be proxied")
  assert(not state.cleared["mcp-session-id"] and not state.cleared["last-event-id"],
    "legacy requests must preserve session and resume headers")
  assert(kong.ctx.shared.mcp_protocol_version == "2025-11-25",
    "legacy initialize must select the client-requested version")
end)

test("legacy initialization rejects unsupported negotiated versions", function()
  local initialize = cjson.encode({
    jsonrpc = "2.0",
    id = 1,
    method = "initialize",
    params = { protocolVersion = "2024-11-05", capabilities = {}, clientInfo = { name = "old", version = "1" } },
  })
  expect_status("unsupported initialize version", 400, {
    body = initialize,
    headers = {
      ["mcp-protocol-version"] = false,
      ["mcp-method"] = false,
    },
  })
end)

test("legacy POST preserves session and Last-Event-ID", function()
  local message = cjson.encode({ jsonrpc = "2.0", id = 2, method = "tools/list", params = {} })
  request({
    body = message,
    headers = {
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-method"] = false,
      ["mcp-name"] = false,
      ["mcp-session-id"] = "legacy-session",
      ["last-event-id"] = "event-17",
    },
  })
  assert(not state.response, "valid legacy POST should be proxied")
  assert(not state.cleared["mcp-session-id"] and not state.cleared["last-event-id"],
    "legacy session and resume headers must reach the upstream")
end)

test("legacy GET requires SSE Accept and preserves optional session", function()
  request({
    method = "GET",
    headers = {
      ["content-type"] = false,
      accept = "text/event-stream",
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-session-id"] = "legacy-session",
      ["last-event-id"] = "event-18",
      ["mcp-method"] = false,
    },
  })
  assert(not state.response, "legacy GET stream should be proxied")
  assert(not state.cleared["mcp-session-id"], "legacy GET must preserve its session identifier")
  assert(not state.cleared["last-event-id"], "legacy GET must preserve its resume cursor")

  expect_status("legacy GET without SSE Accept", 406, {
    method = "GET",
    headers = { accept = "application/json", ["mcp-protocol-version"] = "2025-11-25" },
  })
end)

test("legacy DELETE requires a session and reaches the upstream", function()
  request({
    method = "DELETE",
    headers = {
      ["content-type"] = false,
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-session-id"] = "legacy-session",
      ["mcp-method"] = false,
    },
  })
  assert(not state.response, "valid legacy DELETE should be proxied")
  assert(not state.cleared["mcp-session-id"], "legacy DELETE must preserve its session identifier")

  expect_status("DELETE without session", 400, {
    method = "DELETE",
    headers = { ["content-type"] = false, ["mcp-protocol-version"] = "2025-11-25" },
  })
end)

test("legacy session response headers are retained", function()
  request({
    body = cjson.encode({ jsonrpc = "2.0", id = 3, method = "tools/list", params = {} }),
    headers = {
      ["mcp-protocol-version"] = "2025-11-25",
      ["mcp-method"] = false,
      ["mcp-session-id"] = "legacy-session",
    },
  })
  assert(not state.response, "well-formed legacy request should reach the upstream")
  gateway:header_filter()
  assert(not (state.response_headers_cleared and state.response_headers_cleared["mcp-session-id"]),
    "legacy MCP-Session-Id response header must pass through")
end)

test("scope and audience restrictions still apply", function()
  current_claims = { iss = "https://issuer.example.com/", aud = "wrong", scope = "mcp:access" }
  expect_status("wrong audience", 401)
  current_claims = { iss = "https://issuer.example.com/", aud = "mcp-gateway", scope = "other" }
  expect_status("missing scope", 403)
  assert(state.response.headers["WWW-Authenticate"]:find('error="insufficient_scope"', 1, true),
    "scope failure challenge must identify insufficient scope")
  assert(state.response.headers["WWW-Authenticate"]:find('scope="mcp:access"', 1, true),
    "scope failure challenge must advertise the required scope")
  current_claims = { iss = "https://issuer.example.com/", aud = "mcp-gateway", scope = "mcp:access" }
end)

io.write("1..", passed, "\n")

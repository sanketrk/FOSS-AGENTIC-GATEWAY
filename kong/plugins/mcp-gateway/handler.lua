local cjson = require "cjson.safe"
local openidc = require "resty.openidc"

local Gateway = {
  VERSION = "1.1.0",
  PRIORITY = 800,
}

local PROTOCOL_VERSION_META = "io.modelcontextprotocol/protocolVersion"
local LEGACY_HEADERS = { "Mcp-Session-Id", "Last-Event-ID" }
local CURRENT_PROTOCOL_VERSION = "2026-07-28"
local METADATA_CONTENT_TYPE = "application/json"
local NAME_METHODS = {
  ["tools/call"] = "name",
  ["resources/read"] = "uri",
  ["prompts/get"] = "name",
}

local function has_media_type(header, expected)
  if type(header) == "table" then
    for _, value in ipairs(header) do
      if has_media_type(value, expected) then
        return true
      end
    end
    return false
  end
  if type(header) ~= "string" then
    return false
  end

  for item in header:gmatch("[^,]+") do
    local media_type, parameters = item:match("^%s*([^;]+)%s*(.*)$")
    if media_type and media_type:lower():match("^%s*(.-)%s*$") == expected then
      local qvalue = 1
      for parameter in parameters:gmatch(";([^;]*)") do
        local name, value = parameter:match("^%s*([^=%s]+)%s*=%s*(.-)%s*$")
        if name and name:lower() == "q" then
          qvalue = tonumber(value) or 0
          if qvalue < 0 or qvalue > 1 then
            qvalue = 0
          end
        elseif parameter:match("^%s*[qQ]") then
          qvalue = 0
        end
      end
      if qvalue > 0 then
        return true
      end
    end
  end

  return false
end

local function respond(status, message, headers)
  headers = headers or {}
  headers["Cache-Control"] = "no-store"
  return kong.response.exit(status, { error = message }, headers)
end

local function oauth_challenge(conf, error, scopes)
  local challenge = 'Bearer resource_metadata="' .. conf.resource_metadata_url .. '"'
  if error then
    challenge = challenge .. ', error="' .. error .. '"'
  end
  if scopes and #scopes > 0 then
    challenge = challenge .. ', scope="' .. table.concat(scopes, " ") .. '"'
  end
  return challenge
end

local function protected_resource_metadata(conf)
  return {
    resource = conf.resource_url,
    authorization_servers = (function()
      local issuers = {}
      for _, server in ipairs(conf.authorization_servers) do
        issuers[#issuers + 1] = server.issuer
      end
      return issuers
    end)(),
    scopes_supported = conf.scopes_supported,
    bearer_methods_supported = { "header" },
  }
end

local function header_mismatch(message, request_id)
  local response = {
    jsonrpc = "2.0",
    error = {
      code = -32020,
      message = message,
    },
  }
  if type(request_id) == "string" or type(request_id) == "number" then
    response.id = request_id
  end
  return kong.response.exit(400, response, { ["Cache-Control"] = "no-store" })
end

local function metadata_response(conf)
  local body, err = cjson.encode(protected_resource_metadata(conf))
  if not body then
    kong.log.err("Unable to encode OAuth protected-resource metadata: ", tostring(err))
    return respond(500, "protected-resource metadata unavailable")
  end
  return kong.response.exit(200, body, {
    ["Content-Type"] = METADATA_CONTENT_TYPE,
    ["Cache-Control"] = "public, max-age=300",
  })
end

local function origin_is_allowed(origin, allowed_origins)
  if not origin then
    return true
  end
  if type(origin) ~= "string" then
    return false
  end
  for _, allowed in ipairs(allowed_origins) do
    if origin == allowed then
      return true
    end
  end
  return false
end

local function audience_matches(audience, expected)
  if type(audience) == "string" then
    return audience == expected
  end
  if type(audience) == "table" then
    for _, value in ipairs(audience) do
      if value == expected then
        return true
      end
    end
  end
  return false
end

local function has_required_scopes(claims, required_scopes)
  if #required_scopes == 0 then
    return true
  end

  local granted = {}
  if type(claims.scope) == "string" then
    for scope in claims.scope:gmatch("%S+") do
      granted[scope] = true
    end
  elseif type(claims.scope) == "table" then
    for _, scope in ipairs(claims.scope) do
      if type(scope) == "string" then
        granted[scope] = true
      end
    end
  end

  for _, required in ipairs(required_scopes) do
    if not granted[required] then
      return false
    end
  end
  return true
end

local function header_value_matches(header_value, body_value, allow_base64)
  if type(header_value) ~= "string" or type(body_value) ~= "string" then
    return false
  end

  if allow_base64 then
    local starts_sentinel = header_value:sub(1, 9) == "=?base64?"
    local ends_sentinel = header_value:sub(-2) == "?="
    if starts_sentinel or ends_sentinel then
      if not starts_sentinel or not ends_sentinel then
        return false
      end
      local encoded = header_value:sub(10, -3)
      local decoded = ngx.decode_base64(encoded)
      return decoded ~= nil and decoded == body_value
    end
  end

  if header_value:find("[^ -~]") or header_value:match("^%s") or header_value:match("%s$") then
    return false
  end
  return header_value == body_value
end

local function is_jsonrpc_request_id(id, null_value)
  if id == nil or id == null_value then
    return false
  end
  if type(id) == "string" then
    return true
  end
  return type(id) == "number" and id % 1 == 0
end

local function untrusted_token_issuer(token)
  if #token > 32768 then
    return nil
  end
  local _, payload = token:match("^([^.]+)%.([^.]+)%.([^.]+)$")
  if not payload then
    return nil
  end
  local encoded = payload:gsub("-", "+"):gsub("_", "/")
  local remainder = #encoded % 4
  if remainder == 1 then
    return nil
  elseif remainder > 1 then
    encoded = encoded .. string.rep("=", 4 - remainder)
  end
  local decoded = ngx.decode_base64(encoded)
  if not decoded then
    return nil
  end
  local claims = cjson.decode(decoded)
  return type(claims) == "table" and claims.iss or nil
end

local function find_authorization_server(conf, issuer)
  for _, server in ipairs(conf.authorization_servers) do
    if server.issuer == issuer then
      return server
    end
  end
end

local function is_legacy_version(conf, version)
  for _, supported in ipairs(conf.legacy_protocol_versions or {}) do
    if supported == version then
      return true
    end
  end
  return false
end

local function valid_legacy_session_headers()
  local session_id = kong.request.get_header("mcp-session-id")
  local last_event_id = kong.request.get_header("last-event-id")
  if session_id ~= nil and (type(session_id) ~= "string" or session_id == "" or session_id:find("%c")) then
    return false
  end
  if last_event_id ~= nil and (type(last_event_id) ~= "string" or last_event_id:find("[%c]")) then
    return false
  end
  return true
end

local function validate_transport_request(conf)
  local content_type = kong.request.get_header("content-type")
  local media_type = type(content_type) == "string" and content_type:match("^%s*([^;]+)")
  if not media_type or media_type:lower():match("^%s*(.-)%s*$") ~= "application/json" then
    return respond(415, "POST requires application/json")
  end

  local accept = kong.request.get_header("accept")
  if not has_media_type(accept, "application/json") or not has_media_type(accept, "text/event-stream") then
    return respond(406, "POST Accept must include application/json and text/event-stream")
  end

  local raw_body, body_err = kong.request.get_raw_body()
  if not raw_body then
    kong.log.info("Unable to read MCP request body: ", tostring(body_err))
    return respond(400, "invalid JSON-RPC request body")
  end

  local message, decode_err = cjson.decode(raw_body)
  if type(message) ~= "table" then
    kong.log.info("Unable to decode MCP request body: ", tostring(decode_err))
    return respond(400, "invalid JSON-RPC request body")
  end

  local request_id = message.id
  if message.jsonrpc ~= "2.0" then
    return respond(400, "body must contain one JSON-RPC 2.0 message")
  end

  local is_request = type(message.method) == "string" and message.method ~= ""
    and message.result == nil and message.error == nil
  local is_response = message.method == nil and request_id ~= nil
    and ((message.result ~= nil) ~= (message.error ~= nil)) and
      (message.error == nil or type(message.error) == "table")
  if not is_request and not is_response then
    return respond(400, "body must contain one JSON-RPC request, notification, or response")
  end
  if request_id ~= nil and not is_jsonrpc_request_id(request_id, cjson.null) then
    return respond(400, "JSON-RPC message has an invalid identifier")
  end

  local protocol_header = kong.request.get_header("mcp-protocol-version")
  local params = message.params
  local metadata = type(params) == "table" and params._meta
  local metadata_version = type(metadata) == "table" and metadata[PROTOCOL_VERSION_META]
  local initialize_version = is_request and message.method == "initialize"
    and type(params) == "table" and params.protocolVersion
  local protocol_version = metadata_version == CURRENT_PROTOCOL_VERSION
      and CURRENT_PROTOCOL_VERSION or protocol_header or metadata_version or initialize_version
  if not protocol_version and is_legacy_version(conf, "2025-03-26") then
    protocol_version = "2025-03-26"
  end

  if protocol_version == CURRENT_PROTOCOL_VERSION then
    if not is_request or message.method == "initialize" then
      return respond(400, "message is not valid for the current protocol version")
    end
    local method_header = kong.request.get_header("mcp-method")
    if not header_value_matches(method_header, message.method, false) then
      return header_mismatch("Mcp-Method header does not match the JSON-RPC method", request_id)
    end

    if request_id ~= nil then
      if type(metadata) ~= "table" or type(metadata["io.modelcontextprotocol/clientCapabilities"]) ~= "table"
        or type(protocol_header) ~= "string" or protocol_header == "" or type(metadata_version) ~= "string"
        or protocol_header ~= metadata_version then
        return header_mismatch("MCP-Protocol-Version header does not match request metadata", request_id)
      end
    end

    local name_header = kong.request.get_header("mcp-name")
    local name_field = NAME_METHODS[message.method]
    if name_field then
      local name = type(params) == "table" and params[name_field]
      if type(name) ~= "string" or not header_value_matches(name_header, name, true) then
        return header_mismatch("Mcp-Name header does not match the request name or URI", request_id)
      end
    elseif name_header ~= nil then
      local name = type(params) == "table" and (params.name or params.uri)
      if type(name) ~= "string" or not header_value_matches(name_header, name, true) then
        return header_mismatch("Mcp-Name header does not match the request name or URI", request_id)
      end
    end

    return true, CURRENT_PROTOCOL_VERSION
  end

  if not is_legacy_version(conf, protocol_version) then
    return respond(400, "unsupported MCP protocol version")
  end
  if is_request and message.method == "initialize" then
    local requested_version = type(params) == "table" and params.protocolVersion
    if requested_version ~= protocol_version or not is_legacy_version(conf, requested_version) then
      return respond(400, "initialize requested an unsupported MCP protocol version")
    end
  end
  if protocol_header and not is_legacy_version(conf, protocol_header) then
    return respond(400, "unsupported MCP protocol version")
  end
  if is_request then
    local method_header = kong.request.get_header("mcp-method")
    if method_header ~= nil and not header_value_matches(method_header, message.method, false) then
      return respond(400, "Mcp-Method header does not match the JSON-RPC method")
    end
  end
  if not valid_legacy_session_headers() then
    return respond(400, "invalid MCP session or event identifier")
  end
  return true, protocol_version
end

function Gateway:access(conf)
  local path = kong.request.get_path()
  for _, metadata_path in ipairs(conf.metadata_paths) do
    if path == metadata_path then
      if kong.request.get_method() ~= "GET" then
        return respond(405, "method not allowed", { Allow = "GET" })
      end
      return metadata_response(conf)
    end
  end

  if not origin_is_allowed(kong.request.get_header("origin"), conf.allowed_origins) then
    return respond(403, "origin not allowed")
  end

  local method = kong.request.get_method()
  local protocol_version
  if method == "POST" then
    local valid_request
    valid_request, protocol_version = validate_transport_request(conf)
    if valid_request ~= true then
      return valid_request
    end
  elseif method == "GET" or method == "DELETE" then
    local requested_version = kong.request.get_header("mcp-protocol-version")
    if requested_version == CURRENT_PROTOCOL_VERSION then
      return respond(405, "method not allowed for the current protocol version", { Allow = "POST" })
    end
    protocol_version = requested_version or (is_legacy_version(conf, "2025-03-26") and "2025-03-26")
      or conf.legacy_protocol_versions[1]
    if not is_legacy_version(conf, protocol_version) then
      return respond(405, "method not allowed", { Allow = "POST" })
    end
    if method == "GET" then
      if not has_media_type(kong.request.get_header("accept"), "text/event-stream") then
        return respond(406, "GET requires Accept: text/event-stream")
      end
    elseif not kong.request.get_header("mcp-session-id") or kong.request.get_header("mcp-session-id") == "" then
      return respond(400, "DELETE requires an MCP session identifier")
    end
    if not valid_legacy_session_headers() then
      return respond(400, "invalid MCP session or event identifier")
    end
  else
    return respond(405, "method not allowed", { Allow = "POST, GET, DELETE" })
  end

  kong.ctx.shared.mcp_protocol_version = protocol_version
  if protocol_version == CURRENT_PROTOCOL_VERSION then
    for _, header in ipairs(LEGACY_HEADERS) do
      kong.service.request.clear_header(header)
    end
  end

  local authorization = kong.request.get_header("authorization")
  local scheme, token
  if type(authorization) == "string" then
    scheme, token = authorization:match("^%s*(%S+)%s+(%S+)%s*$")
  end
  if not scheme or scheme:lower() ~= "bearer" or not token then
    return respond(401, "bearer token required", {
      ["WWW-Authenticate"] = oauth_challenge(conf, nil, conf.required_scopes),
    })
  end

  local token_issuer = untrusted_token_issuer(token)
  local authorization_server = token_issuer and find_authorization_server(conf, token_issuer)
  if not authorization_server then
    return respond(401, "invalid bearer token", {
      ["WWW-Authenticate"] = oauth_challenge(conf, "invalid_token"),
    })
  end

  local claims, err = openidc.bearer_jwt_verify({
    discovery = authorization_server.discovery_url,
    token_signing_alg_values_expected = conf.signing_algorithms,
    ssl_verify = conf.ssl_verify and "yes" or "no",
  })
  if type(claims) ~= "table" then
    kong.log.warn("OIDC access-token validation failed: ", tostring(err))
    return respond(401, "invalid bearer token", {
      ["WWW-Authenticate"] = oauth_challenge(conf, "invalid_token"),
    })
  end

  if claims.iss ~= authorization_server.issuer or not audience_matches(claims.aud, conf.audience) then
    return respond(401, "token issuer or audience is invalid", {
      ["WWW-Authenticate"] = oauth_challenge(conf, "invalid_token"),
    })
  end

  if not has_required_scopes(claims, conf.required_scopes) then
    return respond(403, "insufficient scope", {
      ["WWW-Authenticate"] = oauth_challenge(conf, "insufficient_scope", conf.required_scopes),
    })
  end

  if not conf.forward_bearer_token then
    kong.service.request.clear_header("Authorization")
  end
end

function Gateway:header_filter()
  if kong.ctx.shared.mcp_protocol_version == CURRENT_PROTOCOL_VERSION then
    kong.response.clear_header("Mcp-Session-Id")
  end
end

return Gateway

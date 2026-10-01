local json = require "cjson.safe"
local openidc = require "resty.openidc"
local Gateway = { PRIORITY = 800, VERSION = "0.3.0" }

local function contains(values, item)
  for _, value in ipairs(values or {}) do if value == item then return true end end
  return false
end

local function rpc_error(code, message, id)
  return kong.response.exit(400, {
    jsonrpc = "2.0", id = id == nil and json.null or id,
    error = { code = code, message = message },
  }, { ["Cache-Control"] = "no-store" })
end

local function rest_error(status, message, headers, reason)
  local names = {[400]="INVALID_ARGUMENT", [401]="UNAUTHENTICATED", [403]="PERMISSION_DENIED",
    [404]="NOT_FOUND", [405]="UNIMPLEMENTED", [415]="INVALID_ARGUMENT"}
  local error = {code=status, status=names[status], message=message}
  if reason then error.details = {{["@type"]="type.googleapis.com/google.rpc.ErrorInfo",
    reason=reason, domain="a2a-protocol.org"}} end
  return kong.response.exit(status, {error=error}, headers or {["Cache-Control"]="no-store"})
end

-- Exact A2A 1.0 operation paths; never treat arbitrary prefix descendants as A2A.
local function rest_methods(path)
  if path == "/message:send" or path == "/message:stream" then return "POST" end
  if path == "/tasks" or path == "/extendedAgentCard" then return "GET" end
  if path:match("^/tasks/[^/:]+:cancel$") or path:match("^/tasks/[^/:]+:subscribe$") then return "POST" end
  if path:match("^/tasks/[^/:]+/pushNotificationConfigs/[^/:]+$") then return "GET, DELETE" end
  if path:match("^/tasks/[^/:]+/pushNotificationConfigs$") then return "GET, POST" end
  if path:match("^/tasks/[^/:]+$") then return "GET" end
end

local function issuer_of(token)
  if #token > 32768 then return nil end
  local payload = token:match("^[^.]+%.([^.]+)%.[^.]+$")
  if not payload then return nil end
  payload = payload:gsub("-", "+"):gsub("_", "/")
  local n = #payload % 4
  if n == 1 then return nil end
  if n > 1 then payload = payload .. string.rep("=", 4-n) end
  local raw = ngx.decode_base64(payload)
  local claims = raw and json.decode(raw)
  return type(claims) == "table" and claims.iss or nil
end

local function authenticate(conf, rest)
  local function reject(status, body, headers)
    if rest then return rest_error(status, body.message, headers) end
    return kong.response.exit(status, body, headers)
  end
  local header = kong.request.get_header("authorization")
  local scheme, token
  if type(header) == "string" then scheme, token = header:match("^(%S+)%s+(%S+)$") end
  local challenge = { ["WWW-Authenticate"] = "Bearer", ["Cache-Control"] = "no-store" }
  if not scheme or scheme:lower() ~= "bearer" then
    return false, reject(401, {message="Bearer access token required"}, challenge)
  end
  local issuer = issuer_of(token)
  local server
  for _, candidate in ipairs(conf.authorization_servers) do
    if candidate.issuer == issuer then server = candidate; break end
  end
  if not server then
    challenge["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return false, reject(401, {message="Invalid access token"}, challenge)
  end
  -- Unverified issuer only selects an explicit trusted discovery configuration.
  local claims, err = openidc.bearer_jwt_verify({
    discovery=server.discovery_url, ssl_verify="yes",
    token_signing_alg_values_expected=conf.signing_algorithms,
  })
  if err or type(claims) ~= "table" or type(claims.exp) ~= "number"
      or claims.exp ~= claims.exp or claims.exp == math.huge or claims.exp <= ngx.time() or claims.iss ~= server.issuer
      or not (claims.aud == conf.audience or type(claims.aud) == "table" and contains(claims.aud, conf.audience)) then
    challenge["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return false, reject(401, {message="Invalid access token"}, challenge)
  end
  local scopes = {}
  if type(claims.scope) == "string" then for scope in claims.scope:gmatch("%S+") do scopes[scope]=true end end
  if type(claims.scp) == "table" then for _, scope in ipairs(claims.scp) do scopes[scope]=true end end
  for _, scope in ipairs(conf.required_scopes) do
    if not scopes[scope] then
      challenge["WWW-Authenticate"] = 'Bearer error="insufficient_scope"'
      return false, reject(403, {message="Insufficient scope"}, challenge)
    end
  end
  kong.ctx.shared.gateway_authentication = {
    authenticated = true, access_token = token, audience = conf.audience,
  }
  if not conf.forward_bearer_token then kong.service.request.clear_header("Authorization") end
  return true
end

function Gateway:access(conf)
  local path, method = kong.request.get_path(), kong.request.get_method()
  local card = path == conf.card_path
  local rest_path
  if conf.rest_path and path:sub(1, #conf.rest_path + 1) == conf.rest_path .. "/" then
    rest_path = path:sub(#conf.rest_path + 1)
  end
  if not card and not rest_path and path ~= conf.rpc_path then return kong.response.exit(404, {message="Unknown A2A path"}) end
  local origin = kong.request.get_header("origin")
  if origin and (type(origin) ~= "string" or not contains(conf.allowed_origins, origin)) then
    if rest_path then return rest_error(403, "Origin not allowed") end
    return kong.response.exit(403, {message="Origin not allowed"})
  end
  if card then
    if method ~= "GET" then return kong.response.exit(405, {message="Agent Card requires GET"}, {Allow="GET"}) end
    if not conf.public_card then
      local ok, response = authenticate(conf)
      if not ok then return response end
    else
      -- Explicit anonymous discovery must not trigger an outbound token exchange.
      kong.ctx.shared.gateway_authentication = { authenticated = false, audience = conf.audience }
      kong.service.request.clear_header("Authorization")
    end
    -- Pass the card through without rewriting signed content or capabilities.
    kong.service.request.set_path(conf.upstream_card_path)
    return
  end
  if rest_path then
    -- Reject ambiguous encoded/dot paths instead of allowing authorization/routing disagreements.
    if rest_path:find("%%") or rest_path:find("\\") or rest_path:match("/%.[./]?") then
      return rest_error(400, "Invalid REST path")
    end
    local methods = rest_methods(rest_path)
    if not methods then return rest_error(404, "Unknown A2A REST operation") end
    if not (", " .. methods .. ", "):find(", " .. method .. ", ", 1, true) then
      return rest_error(405, "Method not allowed", {Allow=methods})
    end
    local query, err = kong.request.get_query()
    if err or query["A2A-Version"] ~= nil then return rest_error(400, "REST service parameters require headers") end
    local version = kong.request.get_header("a2a-version")
    if version ~= "1.0" or not contains(conf.protocol_versions, version) then
      return rest_error(400, "A2A REST requires supported version 1.0", nil, "VERSION_NOT_SUPPORTED")
    end
    if method == "POST" then
      local content_type = kong.request.get_header("content-type")
      if type(content_type) ~= "string" or content_type:lower():match("^%s*([^;%s]+)") ~= "application/json" then
        return rest_error(415, "REST requires application/json")
      end
      local raw = kong.request.get_raw_body()
      local body = raw and json.decode(raw)
      if type(body) ~= "table" or not raw:match("^%s*{") then return rest_error(400, "JSON object required") end
    end
    local ok, response = authenticate(conf, true)
    if not ok then return response end
    -- Preserve operation, query and body; strip only the configured public mount prefix.
    kong.service.request.set_path((conf.upstream_rest_path or "") .. rest_path)
    return
  end
  if method ~= "POST" then return kong.response.exit(405, {message="A2A JSON-RPC requires POST"}, {Allow="POST"}) end
  local content_type = kong.request.get_header("content-type")
  if type(content_type) ~= "string" or content_type:lower():match("^%s*([^;%s]+)") ~= "application/json" then
    return kong.response.exit(415, {message="JSON-RPC requires application/json"})
  end
  local raw = kong.request.get_raw_body()
  local body = raw and json.decode(raw)
  if body == nil then return rpc_error(-32700, "Invalid JSON payload") end
  local id
  if type(body) == "table" then id = body.id end
  if type(body) ~= "table" or body.jsonrpc ~= "2.0" or type(body.method) ~= "string" or body.method == ""
      or (id ~= nil and id ~= json.null and type(id) ~= "string" and type(id) ~= "number")
      or (body.params ~= nil and (type(body.params) ~= "table" or next(body.params) ~= nil and body.params[1] ~= nil)) then
    return rpc_error(-32600, "Invalid JSON-RPC request")
  end
  local query, query_error = kong.request.get_query()
  if query_error then return rpc_error(-32600, "Invalid query parameters", id) end
  local version = kong.request.get_header("a2a-version")
  local query_version = query["A2A-Version"]
  if query_version ~= nil then
    if type(query_version) ~= "string" or version ~= nil and version ~= query_version then
      return rpc_error(-32600, "Conflicting or repeated A2A version", id)
    end
    version = query_version
  end
  if version == nil or version == "" then version = "0.3" end
  if type(version) ~= "string" or not contains(conf.protocol_versions, version) then
    return rpc_error(-32009, "A2A version not supported", id)
  end
  local ok, response = authenticate(conf)
  if not ok then return response end
  -- No method/parameter conversion: the upstream interprets the selected version.
end

return Gateway

local json = require "cjson.safe"
local openidc = require "resty.openidc"
local Gateway = { PRIORITY = 800, VERSION = "0.1.0" }

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

local function authenticate(conf)
  local header = kong.request.get_header("authorization")
  local scheme, token
  if type(header) == "string" then scheme, token = header:match("^(%S+)%s+(%S+)$") end
  local challenge = { ["WWW-Authenticate"] = "Bearer", ["Cache-Control"] = "no-store" }
  if not scheme or scheme:lower() ~= "bearer" then
    return false, kong.response.exit(401, {message="Bearer access token required"}, challenge)
  end
  local issuer = issuer_of(token)
  local server
  for _, candidate in ipairs(conf.authorization_servers) do
    if candidate.issuer == issuer then server = candidate; break end
  end
  if not server then
    challenge["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return false, kong.response.exit(401, {message="Invalid access token"}, challenge)
  end
  -- Unverified issuer only selects an explicit trusted discovery configuration.
  local claims = openidc.bearer_jwt_verify({
    discovery=server.discovery_url, ssl_verify="yes",
    token_signing_alg_values_expected=conf.signing_algorithms,
  })
  if type(claims) ~= "table" or type(claims.exp) ~= "number" or claims.exp <= ngx.time() or claims.iss ~= server.issuer
      or not (claims.aud == conf.audience or type(claims.aud) == "table" and contains(claims.aud, conf.audience)) then
    challenge["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return false, kong.response.exit(401, {message="Invalid access token"}, challenge)
  end
  local scopes = {}
  if type(claims.scope) == "string" then for scope in claims.scope:gmatch("%S+") do scopes[scope]=true end end
  if type(claims.scp) == "table" then for _, scope in ipairs(claims.scp) do scopes[scope]=true end end
  for _, scope in ipairs(conf.required_scopes) do
    if not scopes[scope] then
      challenge["WWW-Authenticate"] = 'Bearer error="insufficient_scope"'
      return false, kong.response.exit(403, {message="Insufficient scope"}, challenge)
    end
  end
  if not conf.forward_bearer_token then kong.service.request.clear_header("Authorization") end
  return true
end

function Gateway:access(conf)
  local path, method = kong.request.get_path(), kong.request.get_method()
  local card = path == conf.card_path
  if not card and path ~= conf.rpc_path then return kong.response.exit(404, {message="Unknown A2A path"}) end
  local origin = kong.request.get_header("origin")
  if origin and (type(origin) ~= "string" or not contains(conf.allowed_origins, origin)) then
    return kong.response.exit(403, {message="Origin not allowed"})
  end
  if card then
    if method ~= "GET" then return kong.response.exit(405, {message="Agent Card requires GET"}, {Allow="GET"}) end
    if not conf.public_card then
      local ok, response = authenticate(conf)
      if not ok then return response end
    else
      kong.service.request.clear_header("Authorization")
    end
    -- Pass the card through without rewriting signed content or capabilities.
    kong.service.request.set_path(conf.upstream_card_path)
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

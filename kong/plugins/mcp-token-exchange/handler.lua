local http = require "resty.http"
local json = require "cjson.safe"
local ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
local Exchange = { PRIORITY = 700, VERSION = "0.1.0" }

local function fail(status, message)
  -- Never return provider bodies, credentials, or tokens to callers.
  return kong.response.exit(status, { message = message }, { ["Cache-Control"] = "no-store" })
end

local function https_url(value)
  if type(value) ~= "string" or value:find("[%s#]") then return false end
  local authority = value:match("^https://([^/?]+)")
  return authority and not authority:find("@", 1, true)
end

function Exchange:access(conf)
  local token = kong.ctx.shared.mcp_verified_access_token
  if not token or kong.ctx.shared.mcp_verified_resource ~= conf.gateway_resource then
    return fail(500, "Token exchange requires matching MCP gateway authentication")
  end
  -- Remove any original token even when the gateway's forwarding option is enabled.
  kong.service.request.clear_header("Authorization")
  if not https_url(conf.token_endpoint) or not https_url(conf.resource) then
    return fail(500, "Invalid token exchange configuration")
  end
  for _, scope in ipairs(conf.scopes) do
    if scope == "" or scope:find('[%s"\\]') or scope:find("[^%g]") then
      return fail(500, "Invalid token exchange scope configuration")
    end
  end
  local file = io.open(conf.client_secret_file, "r")
  if not file then return fail(503, "Token exchange credentials unavailable") end
  local secret = file:read(4097)
  file:close()
  if not secret or #secret > 4096 then return fail(503, "Token exchange credentials unavailable") end
  secret = secret:gsub("[\r\n]+$", "")
  if secret == "" then return fail(503, "Token exchange credentials unavailable") end
  local form = {
    grant_type = "urn:ietf:params:oauth:grant-type:token-exchange",
    subject_token = token,
    subject_token_type = ACCESS_TOKEN,
    requested_token_type = ACCESS_TOKEN,
    resource = conf.resource,
    scope = table.concat(conf.scopes, " "),
  }
  local headers = { ["Content-Type"] = "application/x-www-form-urlencoded", Accept = "application/json" }
  if conf.client_auth_method == "client_secret_post" then
    form.client_id, form.client_secret = conf.client_id, secret
  else
    -- OAuth client-password authentication percent-encodes each credential first.
    headers.Authorization = "Basic " .. ngx.encode_base64(ngx.escape_uri(conf.client_id) .. ":" .. ngx.escape_uri(secret))
  end
  local client = http.new()
  client:set_timeout(conf.timeout_ms)
  local response = client:request_uri(conf.token_endpoint, {
    method = "POST", body = ngx.encode_args(form), headers = headers,
    ssl_verify = true, keepalive = false,
  })
  -- request_uri does not follow redirects. A redirect must never carry credentials onward.
  if not response or response.status ~= 200 or not response.body or #response.body > 65536 then
    return fail(502, "Token exchange failed")
  end
  local issued = json.decode(response.body)
  if type(issued) ~= "table" or issued.issued_token_type ~= ACCESS_TOKEN
      or type(issued.token_type) ~= "string" or issued.token_type:lower() ~= "bearer"
      or type(issued.access_token) ~= "string" or #issued.access_token == 0
      or #issued.access_token > 16384 or issued.access_token:find("[^A-Za-z0-9%-%._~%+/=]")
      or (issued.expires_in ~= nil and (type(issued.expires_in) ~= "number" or issued.expires_in <= 0)) then
    return fail(502, "Invalid token exchange response")
  end
  if issued.scope ~= nil then
    if type(issued.scope) ~= "string" then return fail(502, "Invalid token exchange response") end
    local granted = {}
    for scope in issued.scope:gmatch("%S+") do granted[scope] = true end
    local requested = {}
    for _, scope in ipairs(conf.scopes) do
      requested[scope] = true
      if not granted[scope] then return fail(502, "Token exchange scope policy not satisfied") end
    end
    for scope in pairs(granted) do
      if not requested[scope] then return fail(502, "Token exchange scope policy not satisfied") end
    end
  end
  kong.service.request.set_header("Authorization", "Bearer " .. issued.access_token)
end

return Exchange

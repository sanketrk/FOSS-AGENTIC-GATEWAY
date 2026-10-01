package.path = "/usr/local/openresty/lualib/?.lua;/usr/local/openresty/lualib/?/init.lua;"
  .. "/usr/local/share/lua/5.1/?.lua;/usr/local/share/lua/5.1/?/init.lua;" .. package.path
package.cpath = "/usr/local/openresty/lualib/?.so;" .. package.cpath
local json = require "cjson.safe"
local state, claims, exchange_calls, exchange_failure, exchange_request
package.preload["resty.openidc"] = function()
  return { bearer_jwt_verify = function() return claims end }
end
package.preload["resty.http"] = function()
  return { new = function() return {
    set_timeout = function() end,
    request_uri = function(_, _, opts)
      exchange_calls = exchange_calls + 1
      exchange_request = opts.body
      if exchange_failure then return nil, "connection failed" end
      return {status=200, body=json.encode({access_token="backend-token", token_type="Bearer",
        issued_token_type="urn:ietf:params:oauth:token-type:access_token", scope="backend:access"})}
    end,
  } end }
end
ngx = {
  time = function() return 1000 end,
  decode_base64 = function() return '{"iss":"https://issuer.example.com/"}' end,
  escape_uri = function(value) return value end,
  encode_base64 = function(value) return value end,
  encode_args = function(value) return value end,
}
kong = {
  ctx = {shared={}},
  request = {
    get_path = function() return state.path end,
    get_method = function() return state.method end,
    get_query = function() return {} end,
    get_header = function(name) return state.headers[name:lower()] end,
    get_raw_body = function() return state.body end,
  },
  service = {request={
    clear_header = function(name) state.upstream_headers[name:lower()]=nil end,
    set_header = function(name, value) state.upstream_headers[name:lower()]=value end,
    set_path = function(path) state.upstream_path=path end,
  }},
  response = {exit=function(status) state.status=status; return status end},
  log = {warn=function() end, info=function() end},
}
local mcp = require "kong.plugins.mcp.handler"
local a2a = require "kong.plugins.a2a.handler"
local exchange = require "kong.plugins.token-exchange.handler"
local authorization_servers={{issuer="https://issuer.example.com/", discovery_url="https://issuer.example.com/discovery"}}
local configs = {
  mcp={audience="https://gateway.example.com/mcp/a", resource_url="https://gateway.example.com/mcp/a",
    resource_metadata_url="https://gateway.example.com/.well-known/oauth-protected-resource/mcp/a",
    metadata_paths={}, allowed_origins={}, authorization_servers=authorization_servers,
    signing_algorithms={"RS256"}, ssl_verify=true, required_scopes={"gateway:access"},
    legacy_protocol_versions={"2025-11-25"}, forward_bearer_token=true},
  a2a={audience="urn:agent:a", rpc_path="/a2a/a", card_path="/cards/a",
    upstream_card_path="/.well-known/agent-card.json", public_card=true,
    allowed_origins={}, authorization_servers=authorization_servers,
    signing_algorithms={"RS256"}, required_scopes={"gateway:access"},
    protocol_versions={"1.0"}, forward_bearer_token=true},
}
local secret_path="/tmp/authentication-exchange-spec-secret"
local file=assert(io.open(secret_path,"w")); file:write("test-secret"); file:close()
local conf={token_endpoint="https://sts.example.com/token", resource="urn:backend:a",
  client_id="gateway", client_secret_file=secret_path, client_auth_method="client_secret_post",
  scopes={"backend:access"}, timeout_ms=3000}
local function reset(config, name)
  kong.ctx.shared={}; exchange_calls=0; exchange_failure=false; exchange_request=nil
  state={method="POST", path=name=="mcp" and "/mcp/a" or config.rpc_path,
    upstream_headers={authorization="Bearer header.trusted.signature"}, headers={
      authorization="Bearer header.trusted.signature", ["content-type"]="application/json",
      accept="application/json, text/event-stream", ["mcp-protocol-version"]="2025-11-25", ["a2a-version"]="1.0"},
    body=json.encode({jsonrpc="2.0", id=1, method=name=="mcp" and "tools/list" or "GetTask", params={}})}
  claims={iss=authorization_servers[1].issuer, aud=config.audience, scope="gateway:access", exp=2000}
  conf.gateway_audience=config.audience
end
for name, plugin in pairs({mcp=mcp, a2a=a2a}) do
  local config=configs[name]
  reset(config,name); plugin:access(config); assert(not state.status)
  exchange:access(conf)
  assert(not state.status and exchange_calls==1 and state.upstream_headers.authorization=="Bearer backend-token")
  assert(exchange_request.subject_token=="header.trusted.signature" and exchange_request.resource=="urn:backend:a")
  reset(config,name); claims.scope="wrong"; plugin:access(config)
  assert(state.status==403 and not kong.ctx.shared.gateway_authentication and exchange_calls==0)
  reset(config,name); plugin:access(config); conf.gateway_audience="other-resource"
  exchange:access(conf); assert(state.status==500 and exchange_calls==0 and not state.upstream_headers.authorization)
  reset(config,name); plugin:access(config); exchange_failure=true
  exchange:access(conf); assert(state.status==502 and not state.upstream_headers.authorization)
end
reset(configs.a2a,"a2a"); state.method="GET"; state.path=configs.a2a.card_path
a2a:access(configs.a2a); exchange:access(conf)
assert(not state.status and exchange_calls==0 and not state.upstream_headers.authorization)
assert(state.upstream_path==configs.a2a.upstream_card_path)
reset(configs.a2a,"a2a"); state.method="GET"; state.path=configs.a2a.card_path; configs.a2a.public_card=false
a2a:access(configs.a2a); exchange:access(conf)
assert(not state.status and exchange_calls==1 and state.upstream_headers.authorization=="Bearer backend-token")
os.remove(secret_path)
print("MCP and A2A authentication-to-exchange chains, anonymous cards, and failure isolation passed.")

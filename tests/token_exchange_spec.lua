package.path = "/usr/local/openresty/lualib/?.lua;/usr/local/openresty/lualib/?/init.lua;"
  .. "/usr/local/share/lua/5.1/?.lua;/usr/local/share/lua/5.1/?/init.lua;" .. package.path
package.cpath = "/usr/local/openresty/lualib/?.so;" .. package.cpath
local json = require "cjson.safe"
local sent, response, upstream, status, calls
package.preload["resty.http"] = function()
  return { new = function() return {
    set_timeout = function(_, timeout) assert(timeout == 3000) end,
    request_uri = function(_, url, opts)
      calls = calls + 1
      sent = opts
      assert(url == "https://sts.example.com/token")
      return response
    end,
  } end }
end
ngx = {
  escape_uri = function(v) return (v:gsub("[^A-Za-z0-9_.~-]", function(c) return string.format("%%%02X", c:byte()) end)) end,
  encode_base64 = function(v) return "encoded:" .. v end,
  encode_args = function(v) return v end,
}
kong = {
  ctx = { shared = {} },
  response = { exit = function(s, body) status = s; assert(not json.encode(body):find("original-token", 1, true)); return s end },
  service = { request = {
    clear_header = function() upstream = nil end,
    set_header = function(_, v) upstream = v end,
  } },
}
package.preload["resty.openidc"] = function() return {} end
local plugin = require "kong.plugins.mcp-token-exchange.handler"
assert(plugin.PRIORITY < require("kong.plugins.mcp-gateway.handler").PRIORITY)
local path = "/tmp/mcp-exchange-test-secret"
local f = assert(io.open(path, "w")); f:write("secret:value\n"); f:close()
local conf = {
  token_endpoint = "https://sts.example.com/token", gateway_resource = "https://gateway.example.com/mcp/a",
  resource = "https://backend.example.com/mcp", client_id = "client:id", client_secret_file = path,
  client_auth_method = "client_secret_basic", scopes = {"tools:read"}, timeout_ms = 3000,
}
local function reset(payload)
  calls, status, sent, upstream = 0, nil, nil, "Bearer original-token"
  kong.ctx.shared = {mcp_verified_access_token = "original-token", mcp_verified_resource = conf.gateway_resource}
  response = {status=200, body=json.encode(payload or {
    access_token="backend-token", token_type="Bearer", issued_token_type="urn:ietf:params:oauth:token-type:access_token",
    expires_in=60, scope="tools:read",
  })}
end
reset(); plugin:access(conf)
assert(not status and calls==1 and upstream=="Bearer backend-token")
assert(sent.ssl_verify==true and sent.keepalive==false and sent.method=="POST")
assert(sent.body.grant_type=="urn:ietf:params:oauth:grant-type:token-exchange")
assert(sent.body.subject_token=="original-token" and sent.body.resource==conf.resource and sent.body.scope=="tools:read")
assert(sent.headers.Authorization=="Basic encoded:client%3Aid:secret%3Avalue")
assert(not sent.body.client_secret)
reset(); conf.client_auth_method="client_secret_post"; plugin:access(conf)
assert(sent.body.client_id==conf.client_id and sent.body.client_secret=="secret:value" and not sent.headers.Authorization)
conf.client_auth_method="client_secret_basic"
reset(); kong.ctx.shared={}; plugin:access(conf); assert(status==500 and calls==0)
reset(); kong.ctx.shared.mcp_verified_resource="https://wrong.example.com"; plugin:access(conf); assert(status==500 and calls==0)
reset(); conf.token_endpoint="http://sts.example.com/token"; plugin:access(conf); assert(status==500 and calls==0 and not upstream)
conf.token_endpoint="https://sts.example.com/token"
reset(); conf.resource="https://evil@example.com/"; plugin:access(conf); assert(status==500 and calls==0)
conf.resource="https://backend.example.com/mcp"
for _, code in ipairs({302,400,401,500}) do
  reset(); response.status=code; plugin:access(conf); assert(status==502 and not upstream)
end
reset(); response=nil; plugin:access(conf); assert(status==502 and not upstream)
reset(); response.body="invalid-json"; plugin:access(conf); assert(status==502 and not upstream)
for _, change in ipairs({
  {"token_type","DPoP"}, {"issued_token_type","urn:ietf:params:oauth:token-type:id_token"},
  {"access_token",""}, {"access_token","injected\r\nHeader:value"}, {"expires_in",0},
  {"scope","other"}, {"scope","tools:read tools:write"},
}) do
  reset(); local body=json.decode(response.body); body[change[1]]=change[2]; response.body=json.encode(body)
  plugin:access(conf); assert(status==502 and not upstream)
end
reset(); local body=json.decode(response.body); body.scope=nil; body.expires_in=nil; response.body=json.encode(body)
plugin:access(conf); assert(not status and upstream=="Bearer backend-token")
reset(); conf.client_secret_file="/tmp/no-such-mcp-exchange-secret"; plugin:access(conf); assert(status==503 and calls==0 and not upstream)
os.remove(path)
print("Token exchange policy, request, response, and failure checks passed.")

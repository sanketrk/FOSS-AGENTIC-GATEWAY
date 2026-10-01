package.path = "/usr/local/openresty/lualib/?.lua;/usr/local/openresty/lualib/?/init.lua;"
  .. "/usr/local/share/lua/5.1/?.lua;/usr/local/share/lua/5.1/?/init.lua;" .. package.path
package.cpath = "/usr/local/openresty/lualib/?.so;" .. package.cpath
local json = require "cjson.safe"
local state, claims, calls, opts
package.preload["resty.openidc"] = function()
  return {bearer_jwt_verify=function(options) calls=calls+1; opts=options; return claims end}
end
ngx = {time=function() return 1000 end, decode_base64=function(value)
  if value == "trusted=" then return '{"iss":"https://issuer.example.com/"}' end
  return '{"iss":"https://attacker.example.com/"}'
end}
kong = {
  request = {
    get_method=function() return state.method end,
    get_path=function() return state.path end,
    get_query=function() return state.query end,
    get_header=function(name) return state.headers[name:lower()] end,
    get_raw_body=function() return state.body end,
  },
  response={exit=function(status,body,headers) state.response={status=status,body=body,headers=headers}; return state.response end},
  service={request={clear_header=function(name) state.cleared[name:lower()]=true end,
    set_path=function(path) state.upstream_path=path end}},
}
local gateway=require "kong.plugins.a2a-gateway.handler"
local config={rpc_path="/a2a/agent-a",card_path="/.well-known/agent-card.json",upstream_card_path="/.well-known/agent-card.json",
  public_card=true,audience="https://gateway.example.com/a2a/agent-a",protocol_versions={"1.0","0.3"},
  authorization_servers={{issuer="https://issuer.example.com/",discovery_url="https://issuer.example.com/.well-known/openid-configuration"}},
  signing_algorithms={"RS256"},required_scopes={"a2a:access"},allowed_origins={"https://client.example.com"},forward_bearer_token=false}
local function reset()
  calls=0; opts=nil
  state={method="POST",path=config.rpc_path,query={},cleared={},headers={
    ["content-type"]="application/json",["a2a-version"]="1.0",authorization="Bearer h.trusted.s"},
    body=json.encode({jsonrpc="2.0",id=7,method="SendMessage",params={message={}}})}
  claims={iss="https://issuer.example.com/",aud=config.audience,scope="a2a:access",exp=1100}
end
local function expect(status)
  gateway:access(config)
  assert(state.response and state.response.status==status, "expected status "..status)
end
reset(); gateway:access(config); assert(not state.response and calls==1 and state.cleared.authorization)
assert(opts.ssl_verify=="yes" and opts.discovery==config.authorization_servers[1].discovery_url)
reset(); state.method="GET"; state.path=config.card_path; state.headers.authorization=nil
gateway:access(config); assert(not state.response and calls==0 and state.upstream_path==config.upstream_card_path)
reset(); state.method="GET"; state.path=config.card_path; config.public_card=false; state.headers.authorization=nil
expect(401); config.public_card=true
reset(); state.path=config.card_path; expect(405)
reset(); state.path="/a2a/unknown"; expect(404)
reset(); state.method="GET"; expect(405)
reset(); state.headers.origin="https://attacker.example.com"; expect(403)
reset(); state.headers["content-type"]="application/jsonp"; expect(415)
reset(); state.body="{"; expect(400); assert(state.response.body.error.code==-32700)
reset(); state.body=json.encode({jsonrpc="2.0",id=false,method="SendMessage"}); expect(400)
reset(); state.body="[]"; expect(400)
reset(); state.body=json.encode({jsonrpc="2.0",id=7,method="SendMessage",params={1,2}}); expect(400)
reset(); state.headers["a2a-version"]="2.0"; expect(400); assert(state.response.body.error.code==-32009 and state.response.body.id==7)
reset(); state.headers["a2a-version"]={"1.0","0.3"}; expect(400)
reset(); state.query["A2A-Version"]="0.3"; expect(400); assert(state.response.body.error.code==-32600)
reset(); state.headers["a2a-version"]=nil; state.query["A2A-Version"]={"1.0","0.3"}; expect(400)
reset(); state.headers["a2a-version"]=nil; state.query["A2A-Version"]="1.0"; gateway:access(config); assert(not state.response)
reset(); state.headers["a2a-version"]=nil; state.body=json.encode({jsonrpc="2.0",id=1,method="message/send",params={}})
gateway:access(config); assert(not state.response)
reset(); config.protocol_versions={"1.0"}; state.headers["a2a-version"]=nil; expect(400); config.protocol_versions={"1.0","0.3"}
reset(); state.headers.authorization=nil; expect(401); assert(calls==0)
reset(); state.headers.authorization="Bearer h.foreign.s"; expect(401); assert(calls==0)
reset(); claims=nil; expect(401)
reset(); claims.aud="other-agent"; expect(401)
reset(); claims.exp=900; expect(401)
reset(); claims.exp=nil; expect(401)
reset(); claims.scope="other"; expect(403)
reset(); claims.aud={config.audience}; claims.scope=nil; claims.scp={"a2a:access"}; gateway:access(config); assert(not state.response)
reset(); state.body=json.encode({jsonrpc="2.0",id=8,method="GetExtendedAgentCard",params={}}); state.headers.authorization=nil; expect(401)
reset(); state.body=json.encode({jsonrpc="2.0",id=8,method="CustomExtension",params={}}); gateway:access(config); assert(not state.response)
reset(); config.forward_bearer_token=true; gateway:access(config); assert(not state.cleared.authorization); config.forward_bearer_token=false
print("A2A transport, discovery, versioning, and authentication checks passed.")

local typedefs = require "kong.db.schema.typedefs"
return {
  name = "a2a",
  fields = {
    { consumer = typedefs.no_consumer },
    { protocols = typedefs.protocols_http },
    { config = { type = "record", fields = {
      { rpc_path = { type = "string", required = true, match = "^/" } },
      { rest_path = { type = "string", match = "^/[^?%%#]+[^/]$" } },
      { upstream_rest_path = { type = "string", len_min = 0, default = "", custom_validator = function(value)
          if value == "" or value:match("^/[^?%%#]+[^/]$") then return true end
          return nil, "Use an empty upstream mount or an absolute path without a trailing slash"
        end } },
      { card_path = { type = "string", default = "/.well-known/agent-card.json", match = "^/" } },
      { upstream_card_path = { type = "string", default = "/.well-known/agent-card.json", match = "^/" } },
      { public_card = { type = "boolean", default = true } },
      { audience = { type = "string", required = true } },
      { protocol_versions = { type = "array", len_min = 1, default = { "1.0", "0.3" }, elements = { type = "string", one_of = { "1.0", "0.3" } } } },
      { signing_algorithms = { type = "array", len_min = 1, default = { "RS256" }, elements = { type = "string", one_of = { "RS256", "RS384", "RS512", "ES256", "ES384" } } } },
      { required_scopes = { type = "array", default = {}, elements = { type = "string" } } },
      { allowed_origins = { type = "array", default = {}, elements = { type = "string" } } },
      { forward_bearer_token = { type = "boolean", default = false } },
          { authorization_servers = {
              type = "array",
              required = true,
              len_min = 1,
              elements = {
                type = "record",
                fields = {
                  { issuer = { type = "string", required = true, match = "^https://" } },
                  { discovery_url = { type = "string", required = true, match = "^https://" } },
                },
              },
            },
          },
    } } },
  },
}

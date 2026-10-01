local typedefs = require "kong.db.schema.typedefs"

return {
  name = "mcp-token-exchange",
  fields = {
    { consumer = typedefs.no_consumer },
    { protocols = typedefs.protocols_http },
    { config = {
      type = "record",
      fields = {
        { token_endpoint = { type = "string", required = true, match = "^https://" } },
        { gateway_resource = { type = "string", required = true, match = "^https://" } },
        { resource = { type = "string", required = true, match = "^https://" } },
        { client_id = { type = "string", required = true } },
        { client_secret_file = { type = "string", required = true, match = "^/" } },
        { client_auth_method = { type = "string", one_of = { "client_secret_basic", "client_secret_post" }, default = "client_secret_basic" } },
        { scopes = { type = "array", required = true, len_min = 1, elements = { type = "string" } } },
        { timeout_ms = { type = "integer", default = 3000, between = { 100, 30000 } } },
      },
    } },
  },
}

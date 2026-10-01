local typedefs = require "kong.db.schema.typedefs"

return {
  name = "mcp",
  fields = {
    { consumer = typedefs.no_consumer },
    { protocols = typedefs.protocols_http },
    { config = {
        type = "record",
        fields = {
          { resource_url = { type = "string", required = true } },
          { resource_metadata_url = { type = "string", required = true } },
          { metadata_paths = {
              type = "array",
              required = true,
              len_min = 1,
              elements = { type = "string" },
            },
          },
          { authorization_servers = {
              type = "array",
              required = true,
              len_min = 1,
              elements = {
                type = "record",
                fields = {
                  { issuer = { type = "string", required = true } },
                  { discovery_url = { type = "string", required = true } },
                },
              },
            },
          },
          { legacy_protocol_versions = {
              type = "array",
              elements = {
                type = "string",
                one_of = { "2025-11-25", "2025-03-26" },
              },
              default = { "2025-11-25", "2025-03-26" },
            },
          },
          { audience = { type = "string", required = true } },
          { scopes_supported = {
              type = "array",
              elements = { type = "string" },
              default = {},
            },
          },
          { forward_bearer_token = { type = "boolean", default = false } },
          { signing_algorithms = {
              type = "array",
              elements = {
                type = "string",
                one_of = { "RS256", "RS384", "RS512", "ES256", "ES384" },
              },
              default = { "RS256" },
            },
          },
          { ssl_verify = { type = "boolean", default = true } },
          { allowed_origins = {
              type = "array",
              elements = { type = "string" },
              default = {},
            },
          },
          { required_scopes = {
              type = "array",
              elements = { type = "string" },
              default = {},
            },
          },
        },
      },
    },
  },
}

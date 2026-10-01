require "yaml"
require "uri"

kong = YAML.load_file("kong/kong.yml")
openshift_config = YAML.load_file("deploy/openshift/kong-config.yaml")
openshift_deployment = YAML.load_file("deploy/openshift/deployment.yaml")
openshift_service = YAML.load_file("deploy/openshift/service.yaml")
openshift_kustomization = YAML.load_file("deploy/openshift/kustomization.yaml")
embedded_kong = YAML.load(openshift_config.fetch("data").fetch("kong.yml"))
embedded_nginx = openshift_config.fetch("data").fetch("nginx-extra.conf")

def assert(condition, message)
  raise message unless condition
end

route_for = lambda do |config|
  service = config.fetch("services").find { |entry| entry.fetch("name") == "mcp-upstream" }
  assert(service, "MCP upstream service is missing")
  route = service.fetch("routes").find { |entry| entry.fetch("name") == "mcp-streamable-http" }
  assert(route, "Streamable HTTP route is missing")
  [service, route]
end

service, route = route_for.call(kong)
embedded_service, embedded_route = route_for.call(embedded_kong)
assert(service == embedded_service, "OpenShift embedded Kong service differs from kong/kong.yml")
assert(route == embedded_route, "OpenShift embedded Kong route differs from kong/kong.yml")
assert(route.fetch("paths").include?("/mcp"), "MCP endpoint path must be /mcp")
assert(route.fetch("paths").include?("/.well-known/oauth-protected-resource"),
  "root protected-resource metadata path must be routed")
assert(route.fetch("paths").include?("/.well-known/oauth-protected-resource/mcp"),
  "path-specific protected-resource metadata must be routed")
assert(!route.key?("methods"), "plugin must receive unsupported methods to return 405")
assert(route.fetch("request_buffering") == false, "request buffering must be disabled")
assert(route.fetch("response_buffering") == false, "response buffering must be disabled")
assert(service.fetch("retries") == 0, "non-idempotent MCP requests must not be retried")

plugin_config = route.fetch("plugins").find { |plugin| plugin.fetch("name") == "mcp-gateway" }.fetch("config")
assert(plugin_config.fetch("forward_bearer_token") == false, "bearer token forwarding must be opt-in")
assert(plugin_config.fetch("signing_algorithms") == ["RS256"], "JWT algorithm default must remain explicit")
assert(plugin_config.fetch("required_scopes").all? { |scope| plugin_config.fetch("scopes_supported").include?(scope) },
  "all required scopes must be represented in advertised scopes")
assert(plugin_config.fetch("legacy_protocol_versions") == ["2025-11-25", "2025-03-26"],
  "legacy protocol versions must remain explicit YAML strings and match supported revisions")
assert(plugin_config.fetch("resource_url").start_with?("https://"), "protected resource URL must use HTTPS")
assert(plugin_config.fetch("resource_metadata_url").start_with?("https://"),
  "protected-resource metadata URL must use HTTPS")
metadata_uri = URI.parse(plugin_config.fetch("resource_metadata_url"))
assert(plugin_config.fetch("metadata_paths").include?(metadata_uri.path),
  "path-specific resource metadata URL must be routed to the metadata handler")
assert(plugin_config.fetch("metadata_paths").include?("/.well-known/oauth-protected-resource"),
  "root resource metadata endpoint must be configured")
assert(plugin_config.fetch("authorization_servers").any?, "at least one IdP must be advertised")
plugin_config.fetch("authorization_servers").each do |authorization_server|
  assert(authorization_server.fetch("issuer").start_with?("https://"), "authorization issuer must use HTTPS")
  assert(authorization_server.fetch("discovery_url").start_with?("https://"),
    "authorization server discovery URL must use HTTPS")
end

expected_dicts = [
  "lua_shared_dict discovery 5m;",
  "lua_shared_dict jwks 5m;",
  "lua_shared_dict jwt_verification 5m;",
]
nginx_file = File.read("kong/nginx-extra.conf")
expected_dicts.each do |directive|
  assert(nginx_file.lines.map(&:strip).include?(directive), "missing Nginx shared-dictionary directive: #{directive}")
  assert(embedded_nginx.lines.map(&:strip).include?(directive), "OpenShift ConfigMap is missing: #{directive}")
end

dockerfile = File.read("Dockerfile")
assert(dockerfile.include?("COPY kong/nginx-extra.conf /etc/kong/nginx-extra.conf"),
  "container image must include the OpenID cache dictionaries")
assert(dockerfile.include?("KONG_NGINX_HTTP_INCLUDE=/etc/kong/nginx-extra.conf"),
  "container image must load the OpenID cache dictionaries")

pod_spec = openshift_deployment.dig("spec", "template", "spec")
container = pod_spec.fetch("containers").find { |entry| entry.fetch("name") == "gateway" }
security = container.fetch("securityContext")
assert(pod_spec.fetch("automountServiceAccountToken") == false, "service account token must not be mounted")
assert(pod_spec.dig("securityContext", "runAsNonRoot") == true, "pod must run as non-root")
assert(security.fetch("allowPrivilegeEscalation") == false, "privilege escalation must be disabled")
assert(security.fetch("readOnlyRootFilesystem") == true, "container root filesystem must be read-only")
assert(security.dig("capabilities", "drop") == ["ALL"], "container must drop all capabilities")
assert(container.fetch("env").any? { |entry| entry["name"] == "KONG_NGINX_HTTP_INCLUDE" && entry["value"] == "/etc/kong/nginx-extra.conf" },
  "custom shared-dictionary include must be configured")
assert(container.fetch("env").any? { |entry| entry["name"] == "KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE" && entry["value"] == "10m" },
  "request body limit must be explicitly set")
config_mount = container.fetch("volumeMounts").find { |mount| mount["name"] == "kong-config" }
assert(config_mount && config_mount["mountPath"] == "/etc/kong" && config_mount["readOnly"] == true,
  "Kong config and Nginx include must be mounted read-only")
assert(openshift_service.dig("spec", "ports").map { |entry| entry.fetch("port") } == [8000],
  "only the Kong proxy listener may be exposed by the Service")
assert(openshift_kustomization.fetch("resources").sort == %w[deployment.yaml kong-config.yaml service.yaml].sort,
  "Kustomize must include all gateway resources")

puts "Kong and OpenShift configuration checks passed."

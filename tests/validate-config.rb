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

assert(kong == embedded_kong, "OpenShift embedded Kong config differs from kong/kong.yml")
services = kong.fetch("services")
assert(services.length >= 3, "sample must include default and two named MCP services")
seen_paths = {}
seen_resources = {}
seen_audiences = {}
services.each do |service|
  service.fetch("routes").each do |route|
    assert(!route.key?("methods"), "plugin must receive unsupported methods to return 405")
    assert(route.fetch("request_buffering") == false, "request buffering must be disabled")
    assert(route.fetch("response_buffering") == false, "response buffering must be disabled")
    assert(service.fetch("retries") == 0, "non-idempotent MCP requests must not be retried")
    config = route.fetch("plugins").find { |plugin| plugin.fetch("name") == "mcp-gateway" }.fetch("config")
    resource = URI.parse(config.fetch("resource_url"))
    metadata = URI.parse(config.fetch("resource_metadata_url"))
    assert(resource.scheme == "https" && metadata.scheme == "https", "public resource URLs must use HTTPS")
    assert(config.fetch("audience") == resource.to_s, "MCP audience must match its public resource URI")
    assert(!seen_resources[resource.to_s], "resource must be unique per service")
    assert(!seen_audiences[config.fetch("audience")], "sample audiences must isolate services")
    seen_resources[resource.to_s] = true
    seen_audiences[config.fetch("audience")] = true
    assert(config.fetch("metadata_paths").include?(metadata.path), "resource metadata URL must reach its handler")
    expected_paths = [resource.path] + config.fetch("metadata_paths")
    assert(route.fetch("paths").sort == expected_paths.map { |path| "~#{path}$" }.sort,
      "routes must match exactly their resource and metadata paths")
    expected_paths.each do |path|
      assert(!seen_paths[path], "path #{path} belongs to multiple services")
      seen_paths[path] = true
    end
    if service.fetch("name") != "mcp-upstream"
      assert(route.fetch("strip_path") && route.fetch("path_handling") == "v0", "named services must strip public route paths")
      assert(URI.parse(service.fetch("url")).path == "/mcp", "named backends must receive /mcp")
    end
    assert(config.fetch("forward_bearer_token") == false, "bearer token forwarding must be opt-in")
    assert(config.fetch("signing_algorithms") == ["RS256"], "JWT algorithm must remain explicit")
    assert(config.fetch("required_scopes").all? { |scope| config.fetch("scopes_supported").include?(scope) },
      "required scopes must be advertised")
    assert(config.fetch("legacy_protocol_versions") == ["2025-11-25", "2025-03-26"], "legacy revisions must be explicit strings")
    assert(config.fetch("authorization_servers").any?, "at least one IdP must be advertised")
    config.fetch("authorization_servers").each do |server|
      assert(URI.parse(server.fetch("issuer")).scheme == "https", "issuer must use HTTPS")
      assert(URI.parse(server.fetch("discovery_url")).scheme == "https", "discovery URL must use HTTPS")
    end
  end
end
assert(seen_paths["/.well-known/oauth-protected-resource"], "default root metadata must remain available")
%w[server-a server-b].each do |name|
  assert(seen_paths["/mcp/#{name}"], "missing named MCP endpoint #{name}")
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

require "yaml"
resources = YAML.load_stream(File.read("deploy/control-plane/control-plane.yaml"))
def assert(value, message)
  raise message unless value
end
role = resources.find { |r| r["kind"] == "Role" }
assert(role["rules"].length == 2, "publisher role must have exactly two rules")
role["rules"].each do |rule|
  assert(rule["verbs"].sort == %w[get patch], "publisher must only read and patch")
  assert(rule["resourceNames"].length == 1, "publisher permissions must target named resources")
end
assert(role["rules"].find { |r| r["resources"] == ["configmaps"] }["resourceNames"] == ["mcp-gateway-kong"], "unexpected ConfigMap permission")
assert(role["rules"].find { |r| r["resources"] == ["deployments"] }["resourceNames"] == ["mcp-gateway"], "unexpected Deployment permission")
deployment = resources.find { |r| r["kind"] == "Deployment" }
assert(deployment.dig("spec", "replicas") == 1, "SQLite writer must be a single replica")
assert(deployment.dig("spec", "strategy", "type") == "Recreate", "SQLite writers must not overlap during rollout")
pod = deployment.dig("spec", "template", "spec")
assert(pod["serviceAccountName"] == "mcp-control-plane", "use a dedicated publisher identity")
container = pod["containers"].first
security = container["securityContext"]
assert(security["readOnlyRootFilesystem"] && security["allowPrivilegeEscalation"] == false, "control plane must be hardened")
assert(security.dig("capabilities", "drop") == ["ALL"], "control plane must drop capabilities")
assert(container["env"].find { |e| e["name"] == "CONTROL_PLANE_TOKEN" }.dig("valueFrom", "secretKeyRef", "name") == "mcp-control-plane-auth", "admin token must come from a Secret")
assert(pod["volumes"].first.dig("persistentVolumeClaim", "claimName") == "mcp-control-plane-data", "registry must persist")
puts "Optional control plane deployment checks passed."

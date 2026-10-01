require "yaml"
require "uri"
config = YAML.load_file("examples/a2a-kong.yml")
service = config.fetch("services").first
route = service.fetch("routes").first
plugin = route.fetch("plugins").first
raise "incorrect plugin" unless plugin.fetch("name") == "a2a-gateway"
policy = plugin.fetch("config")
raise "A2A operations must not retry" unless service.fetch("retries") == 0
raise "A2A SSE must not buffer" unless route.fetch("request_buffering") == false && route.fetch("response_buffering") == false
raise "HTTP method errors must reach plugin" if route.key?("methods")
raise "example path mismatch" unless route.fetch("paths").sort == [policy.fetch("rpc_path"), policy.fetch("card_path")].map { |path| "~#{path}$" }.sort
raise "invalid audience" unless URI(policy.fetch("audience")).scheme == "https" && URI(policy.fetch("audience")).path == policy.fetch("rpc_path")
raise "explicit versions required" unless policy.fetch("protocol_versions") == ["1.0", "0.3"]
raise "token forwarding must be opt-in" unless policy.fetch("forward_bearer_token") == false
raise "image missing plugin" unless File.read("Dockerfile").include?("COPY kong/plugins/a2a-gateway")
env = YAML.load_file("deploy/openshift/deployment.yaml").dig("spec", "template", "spec", "containers").first.fetch("env")
raise "deployment missing plugin" unless env.find { |item| item["name"] == "KONG_PLUGINS" }.fetch("value").split(",").include?("a2a-gateway")
puts "A2A example and deployment checks passed."

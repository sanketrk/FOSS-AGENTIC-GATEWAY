require "yaml"

config = YAML.load_file("examples/banking/kong.yml")
services = config.fetch("services")
raise "expected review agent and two MCP servers" unless services.size == 3
services.each do |service|
  route = service.fetch("routes").first
  plugins = route.fetch("plugins")
  auth = plugins.first.fetch("config")
  exchange = plugins.last.fetch("config")
  raise "missing exchange" unless plugins.last["name"] == "token-exchange"
  raise "audience mismatch" unless auth.fetch("audience") == exchange.fetch("gateway_audience")
  raise "unchanged token audience" if exchange.fetch("resource") == auth.fetch("audience")
  raise "original token forwarding enabled" unless auth["forward_bearer_token"] == false
  raise "buffering/retries enabled" unless service["retries"] == 0 && route["request_buffering"] == false && route["response_buffering"] == false
end
puts "Banking gateway examples use isolated audiences and outbound exchange policies."

compose_source = File.read("examples/banking/compose.yml")
compose = YAML.respond_to?(:unsafe_load) ? YAML.unsafe_load(compose_source) : YAML.load(compose_source)
components = {"banking-orchestrator" => "banking-orchestrator", "review-agent" => "transaction-review-agent",
              "accounts" => "accounts-mcp-server", "transactions" => "transactions-mcp-server"}
images = components.map do |service, folder|
  component = compose.fetch("services").fetch(service)
  raise "incorrect component build" unless component.fetch("build").fetch("dockerfile") == "examples/banking/#{folder}/Dockerfile"
  raise "missing component documentation" unless File.exist?("examples/banking/#{folder}/README.md")
  component.fetch("image")
end
raise "components must own separate images" unless images.uniq.size == 4
puts "Four banking components have separate image builds and documentation."

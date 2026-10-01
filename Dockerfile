ARG KONG_VERSION=3.9.0
FROM kong:${KONG_VERSION}

USER root

RUN luarocks --lua-version=5.1 install --deps-mode=none \
    https://luarocks.org/lua-resty-openidc-1.8.0-1.src.rock

COPY kong/plugins/mcp-gateway /usr/local/share/lua/5.1/kong/plugins/mcp-gateway
COPY kong/kong.yml /etc/kong/kong.yml
COPY kong/nginx-extra.conf /etc/kong/nginx-extra.conf

ENV KONG_DATABASE=off \
    KONG_DECLARATIVE_CONFIG=/etc/kong/kong.yml \
    KONG_ADMIN_LISTEN=off \
    KONG_PROXY_LISTEN=0.0.0.0:8000 \
    KONG_STATUS_LISTEN=0.0.0.0:8100 \
    KONG_PREFIX=/tmp/kong \
    KONG_PLUGINS=bundled,mcp-gateway \
    KONG_NGINX_HTTP_INCLUDE=/etc/kong/nginx-extra.conf \
    KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE=10m \
    KONG_LUA_SSL_TRUSTED_CERTIFICATE=/etc/ssl/certs/ca-certificates.crt

USER 1001
EXPOSE 8000
CMD ["kong", "docker-start"]

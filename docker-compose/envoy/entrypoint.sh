#!/bin/sh
# Substitute ENVOY_API_KEY into the config template, then start Envoy.
# Default to placeholder_api_key if not set.
export ENVOY_API_KEY="${ENVOY_API_KEY:-placeholder_api_key}"
envsubst '${ENVOY_API_KEY}' < /etc/envoy/envoy.yaml.tmpl > /tmp/envoy.yaml
exec envoy -c /tmp/envoy.yaml "$@"

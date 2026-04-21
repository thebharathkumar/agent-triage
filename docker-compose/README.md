# Triage Observability Pipeline

This directory contains a full Docker Compose observability stack built around the `triage` scoring engine. It is heavily inspired by and fully compatible with the architecture of `prove-ai/observability-pipeline`.

## Architecture

```text
[Agent Traces] --(OTLP)--> [Envoy Proxy]
                                |
                                | (auth)
                                v
                        [OTel Collector]
                       /                \
          (spanmetrics)                  (file-exporter)
             /                              \
       [Prometheus]                       [Shared Volume]
            |                               |
    [VictoriaMetrics]               [Triage Service]
                                            |
                                            v
                                   [Triage UI & API]
```

## Prerequisites

- Docker and Docker Compose
- (Optional) `otel-cli` for sending test OTLP spans

## Running the Stack

The pipeline uses Docker Compose **profiles** so you can run exactly what you need.

```bash
cd docker-compose

# Run the complete stack
docker compose --profile full up -d

# Stop everything
docker compose --profile full down -v
```

### Available Profiles

| Profile | Description | Included Services |
|---------|-------------|-------------------|
| `full` | The complete observability stack | Envoy, OTel, Prometheus, VictoriaMetrics, Triage |
| `triage-only` | Just the Triage Service reading from local files. Best for zero-infra demos. | Triage Service |
| `no-vm` | Run without VictoriaMetrics (Prometheus only storage) | Envoy, OTel, Prometheus, Triage |
| `no-triage` | Mirror the prove-ai stack exactly, without the Triage layer | Envoy, OTel, Prometheus, VictoriaMetrics |

## Authentication

All external traffic flows through **Envoy Proxy**, which provides API Key authentication.

1. Copy `.env.example` to `.env`
2. Set your `ENVOY_API_KEY` (defaults to `placeholder_api_key`)
3. Send requests with the header `X-API-Key: <your-key>`

## The Triage Service

The `triage-service` is a FastAPI wrapper around the `triage` Python CLI built in the root of this repo. It runs the scoring model over trace events and serves a live report.

- **UI Dashboard**: `http://localhost:7070/`
- **JSON API**: `http://localhost:7070/api/report`
- **Markdown Report**: `http://localhost:7070/report`

### Where does it get data?

The Triage Service reads from two places:
1. **The `span_data` volume**: The OTel Collector's file exporter writes accepted OTLP spans here as NDJSON.
2. **The `../runs` directory**: Any `.ndjson` files you drop into the local `runs/` folder will be picked up automatically.

The service runs the scoring engine continuously in the background (default: every 60 seconds) and updates the UI.

## Testing Ingestion

You can send a test span using `otel-cli`:

```bash
otel-cli span \
  --service "triage-test" \
  --name "demo-span" \
  --endpoint http://localhost:4318/v1/traces \
  --protocol http/protobuf \
  --attrs "agent_id=AgentB,failure_classification=agent_error" \
  --start "$(date -Iseconds)" \
  --end "$(date -Iseconds)" \
  --headers "X-API-Key: placeholder_api_key"
```

Once accepted, the span will flow through the OTel Collector, be written to the shared volume, and be scored by the Triage Service within 60 seconds.

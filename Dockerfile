# syntax=docker/dockerfile:1.6

# ---- Build stage --------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tooling
RUN pip install --no-cache-dir build

# Copy only what's needed to build the wheel
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m build --wheel --outdir /wheels


# ---- Runtime stage ------------------------------------------------------
FROM python:3.11-slim AS runtime

# Run as non-root for security
RUN useradd --create-home --shell /bin/bash triage

WORKDIR /app

# Install the wheel with server + ai extras
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir "/tmp/$(ls /tmp | grep '\.whl$')[server,ai]" \
    && rm /tmp/*.whl

USER triage

EXPOSE 8000

# Liveness probe target
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/events/count')" || exit 1

CMD ["triage-serve", "--host", "0.0.0.0", "--port", "8000"]

FROM node:22-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS frontend
WORKDIR /ui
COPY web/ui/package*.json ./
RUN npm ci
COPY web/ui/ ./
RUN npm run build

FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
WORKDIR /workspace
COPY requirements.lock pyproject.toml README.md ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src/ src/
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .
COPY configs/ configs/
COPY benchmarks/ benchmarks/
COPY docs/ docs/
COPY resources/ resources/
COPY qa/ qa/
COPY tests/ tests/
COPY docker/ docker/
COPY scripts/ scripts/
COPY docker-compose.yml docker-compose.dev.yml docker-compose.openmc.yml ./
COPY web/ui/package-lock.json web/ui/package-lock.json
COPY web/README.md web/README.md
COPY --from=frontend /ui/dist/ web/ui/dist/
RUN python scripts/build_manifest.py > build-manifest.json
ENV PYTHONPATH=/workspace/src
CMD ["python", "-m", "thorium_reactor.cli", "--help"]

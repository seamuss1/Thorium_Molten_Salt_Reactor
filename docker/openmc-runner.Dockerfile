FROM openmc/openmc:latest@sha256:efe6796f94fd0c0341f8a419603f97d7d66bc8a02acdfb4bd14c589d2d51ca7b
WORKDIR /workspace
# The pinned base includes OpenMC 0.15.3, its compiled dependencies and NNDC data.
COPY requirements.lock pyproject.toml README.md ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src/ src/
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .
COPY configs/ configs/
COPY benchmarks/ benchmarks/
COPY resources/ resources/
COPY qa/ qa/
COPY docs/ docs/
COPY docker/ docker/
COPY scripts/build_manifest.py scripts/build_manifest.py
COPY web/ui/package-lock.json web/ui/package-lock.json
COPY web/README.md web/README.md
RUN python scripts/build_manifest.py > build-manifest.json
ENV PYTHONPATH=/workspace/src
CMD ["python", "-m", "thorium_reactor.cli", "--help"]

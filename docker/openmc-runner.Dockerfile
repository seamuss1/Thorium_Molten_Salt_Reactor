FROM openmc/openmc:latest

WORKDIR /workspace

# The base image already includes OpenMC and nuclear data. Add the few
# dependencies our repo needs for config loading and report/geometry output.
RUN python -m pip install --no-cache-dir \
    "PyYAML>=6.0" \
    "matplotlib>=3.8"

ENV PYTHONPATH=/workspace/src

CMD ["python", "-m", "thorium_reactor.cli", "--help"]

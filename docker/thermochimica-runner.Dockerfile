FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        "PyYAML>=6.0" \
        "numpy>=1.26" \
        "Pillow>=10.0" \
        "matplotlib>=3.8"

ENV PYTHONPATH=/workspace/src
ENV THORIUM_REACTOR_TOOL_RUNTIME=thermochimica

CMD ["python", "-m", "thorium_reactor.cli", "--help"]

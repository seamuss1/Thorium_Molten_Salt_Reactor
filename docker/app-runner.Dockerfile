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
        "fastapi>=0.110" \
        "httpx>=0.27" \
        "uvicorn[standard]>=0.29" \
        "matplotlib>=3.8" \
        "pytest>=8.0"

ENV PYTHONPATH=/workspace/src

CMD ["python", "-m", "thorium_reactor.cli", "--help"]

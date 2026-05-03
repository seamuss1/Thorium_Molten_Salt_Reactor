FROM python:3.11-slim

ARG PYTORCH_XPU_INDEX_URL=https://download.pytorch.org/whl/xpu
ARG PYTORCH_XPU_VERSION=2.11.0+xpu

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg git libgl1 libglib2.0-0 libgomp1 \
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

RUN python -m pip install --no-cache-dir \
        --index-url "${PYTORCH_XPU_INDEX_URL}" \
        --extra-index-url https://pypi.org/simple \
        "torch==${PYTORCH_XPU_VERSION}"

ENV PYTHONPATH=/workspace/src \
    PYTORCH_ENABLE_XPU_FALLBACK=0 \
    SYCL_CACHE_PERSISTENT=1 \
    ZE_ENABLE_PCI_ID_DEVICE_ORDER=1 \
    KMP_DUPLICATE_LIB_OK=TRUE

CMD ["python", "-m", "thorium_reactor.cli", "--help"]

# Build stage: evdev ships only as source, so compile it against the kernel input headers
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
# pip is not needed at runtime and vendors its own (scanner-flagged) libraries
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index /wheels/*.whl \
    && python -m pip uninstall --yes pip
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY macropad_bridge.py healthcheck.py ./
RUN useradd --system --uid 1000 --no-create-home macropad
USER 1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]
CMD ["python", "/app/macropad_bridge.py"]

# Test stage: CI builds this target on every platform before publishing
FROM runtime AS test
COPY tests/ tests/
RUN python -m unittest discover -s tests -v

FROM runtime

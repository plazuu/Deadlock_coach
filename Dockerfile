# syntax=docker/dockerfile:1

# ---- build: resolve dependencies + install the package into a venv ----------
FROM python:3.13-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip

# Dependency layer: pyproject.toml is the single source of truth. Install with a
# stub package so this layer is cached until the dependency list changes.
COPY pyproject.toml README.md ./
RUN mkdir -p src/limpet && touch src/limpet/__init__.py \
    && /opt/venv/bin/pip install . \
    && /opt/venv/bin/pip uninstall -y limpet

# App layer.
COPY src ./src
RUN /opt/venv/bin/pip install --no-deps .

# ---- runtime --------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LIMPET_DATA_DIR=/data

RUN useradd --create-home --uid 1000 limpet \
    && mkdir -p /data && chown limpet:limpet /data

COPY --from=build /opt/venv /opt/venv

USER limpet
WORKDIR /home/limpet
VOLUME ["/data"]

# All configuration can come from the environment, so no interactive `init` is
# needed in a container:
#   LIMPET_STEAM_ID, LIMPET_DEADLOCK_API_KEY, LIMPET_ANTHROPIC_API_KEY
ENTRYPOINT ["limpet"]
CMD ["--help"]

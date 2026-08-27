# The application image: CPU-only, no CUDA, no NVIDIA runtime.
#
# The GPU image is F20's and lives beside the Model Service; nothing here may grow a
# dependency on it. The base install is pure-Python, so this image builds offline-fast and
# `tourganize doctor` works in it with no extras at all — the `terminal` extra is included
# because F07's surface is the first thing a human runs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOURGANIZE_CONFIG_DIR=/app/config \
    TOURGANIZE_FIXTURE_DIR=/app/fixtures/options \
    TOURGANIZE_DATA_DIR=/var/lib/tourganize

# Non-root from the start: the data directory is the only writable path the app needs.
RUN useradd --create-home --uid 10001 tourganize \
 && mkdir -p /var/lib/tourganize \
 && chown -R tourganize:tourganize /var/lib/tourganize

WORKDIR /app

# Install from the metadata first so a source-only change does not reinstall the world.
COPY pyproject.toml README.md LICENSE ./
COPY tourganize ./tourganize
RUN pip install --no-cache-dir -e ".[terminal]"

# Configuration and fixture data are both *data* the application reads at run time, and both
# are copied after the install so that editing either does not reinstall the package. The
# fixtures are what makes `tourganize options search` work in the image with no accounts, no
# keys and no network — D9's whole argument, in one COPY.
COPY config ./config
COPY fixtures ./fixtures

USER tourganize

# No ENTRYPOINT, so `docker compose run --rm app tourganize doctor` works verbatim and any
# other command can be run in the image without fighting an entrypoint.
CMD ["tourganize", "doctor"]

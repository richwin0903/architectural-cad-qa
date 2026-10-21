ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS frontend
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /build/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci --registry="${NPM_REGISTRY}"
COPY web/frontend/ ./
COPY fonts/NotoSansSC-Regular.ttf /build/fonts/NotoSansSC-Regular.ttf
RUN npm run test:font && npm run build

FROM ${PYTHON_IMAGE} AS runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src:/app \
    MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib HOME=/tmp
WORKDIR /app
COPY requirements-production.txt ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements-production.txt \
    && groupadd --gid 10001 cad \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin cad \
    && install -d -o 10001 -g 10001 -m 0700 /data \
    && touch /data/.volume-owner \
    && chown 10001:10001 /data/.volume-owner

FROM runtime AS libredwg-builder
ARG DEBIAN_MIRROR=
ARG BUILD_JOBS=1
RUN if [ -n "${DEBIAN_MIRROR}" ]; then \
      sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update -qq \
    && apt-get install -y --no-install-recommends build-essential pkg-config xz-utils gpgv \
    && rm -rf /var/lib/apt/lists/*
COPY vendor/libredwg-0.14.tar.xz vendor/libredwg-0.14.tar.xz.sig vendor/gnu-keyring.gpg /tmp/libredwg-source/
RUN echo '62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275  /tmp/libredwg-source/libredwg-0.14.tar.xz' | sha256sum -c - \
    && gpgv --keyring /tmp/libredwg-source/gnu-keyring.gpg /tmp/libredwg-source/libredwg-0.14.tar.xz.sig /tmp/libredwg-source/libredwg-0.14.tar.xz \
    && mkdir -p /build \
    && tar -xJf /tmp/libredwg-source/libredwg-0.14.tar.xz -C /build --strip-components=1
WORKDIR /build
RUN CFLAGS='-O1 -g0' ./configure --disable-bindings --disable-docs --disable-shared --enable-static --enable-release \
    && make -j"${BUILD_JOBS}" \
    && strip programs/dwg2dxf \
    && programs/dwg2dxf --version

FROM runtime AS app
COPY --from=libredwg-builder /build/programs/dwg2dxf /usr/local/bin/
COPY --from=libredwg-builder /build/COPYING /usr/local/share/licenses/libredwg/COPYING
COPY --from=frontend /build/web/frontend/dist/ web/frontend/dist/
COPY src/ src/
COPY web/backend/ web/backend/
COPY local_examples/ local_examples/
COPY fonts/ fonts/
COPY LICENSE ./
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "web.backend.production:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--log-level", "warning", "--timeout-keep-alive", "10", "--limit-concurrency", "32"]

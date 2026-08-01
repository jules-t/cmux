FROM node:24.9.0-bookworm-slim@sha256:3e69116c924bfcba6c6979aff60d966c37aef56d488ce091c69d442ebec9f103

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        git \
        python3 \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/pi
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY run_agent.mjs ./
RUN npm run check

ENTRYPOINT ["node", "/opt/pi/run_agent.mjs"]

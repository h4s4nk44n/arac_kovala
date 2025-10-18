FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

## Install Google Chrome stable and runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg gnupg2 gpg-agent apt-transport-https && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
    fonts-liberation fonts-noto-color-emoji fonts-noto-cjk \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libcairo2 libcups2 libdbus-1-3 libdrm2 \
    libexpat1 libfontconfig1 libgdk-pixbuf-2.0-0 libglib2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libgbm1 libxshmfence1 xdg-utils xvfb xauth \
    libu2f-udev \
    scrot python3-tk python3-dev && \
    rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome \
    SB_CHROME_BINARY=/usr/bin/google-chrome \
    HEADLESS=1 \
    DBUS_SESSION_BUS_ADDRESS=/dev/null

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt
COPY . /app

# Recommend environment flags for Chrome inside containers
ENV EXTRA_CHROME_ARGS="--single-process,--disable-dev-shm-usage,--no-sandbox,--no-zygote"

CMD ["bash", "-lc", "exec gunicorn -w 1 -k gthread -b 0.0.0.0:${PORT:-8080} app:app"]
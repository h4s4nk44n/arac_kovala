FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    HEADLESS=1

# Playwright/patchright runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget \
    fonts-liberation fonts-noto-color-emoji fonts-noto-cjk \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libcairo2 libcups2 libdbus-1-3 libdrm2 \
    libexpat1 libfontconfig1 libgdk-pixbuf-2.0-0 libglib2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libgbm1 libxshmfence1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    scrapling install && \
    python -m patchright install chromium

COPY . /app

CMD gunicorn -w 1 -k gthread --bind 0.0.0.0:${PORT:-8080} --timeout 300 --access-logfile - --error-logfile - --log-level info app:app

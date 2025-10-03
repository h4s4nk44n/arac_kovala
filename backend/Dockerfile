FROM python:3.11-slim-bookworm

# Prevents Python from writing .pyc files and enables unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

# Install Chromium, Xvfb, and all dependencies for a virtual display environment.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libcairo2 libcups2 libdbus-1-3 libdrm2 \
    libexpat1 libfontconfig1 libgdk-pixbuf-2.0-0 libglib2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libgbm1 libxshmfence1 xdg-utils wget ca-certificates xvfb xauth && \
    rm -rf /var/lib/apt/lists/*

# Tell Selenium to use Chromium and ensure it doesn't try to run headless on its own.
ENV CHROME_BIN=/usr/bin/chromium \
    SB_CHROME_BINARY=/usr/bin/chromium \
    HEADLESS=0

WORKDIR /app

# Install Python dependencies first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy the backend app
COPY . /app

# Run as root; Chrome will use --no-sandbox via app config
# Gunicorn will start the application
CMD ["bash", "-lc", "exec gunicorn -w 1 -k gthread -b 0.0.0.0:${PORT:-8080} app:app"]
FROM python:3.11-slim

# Prevents Python from writing .pyc files and enables unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

# Install Chromium (avoids Google apt signing issues) + common runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    fonts-liberation \
    libasound2 libatk1.0-0 libcairo2 libcups2 libdbus-1-3 \
    libexpat1 libfontconfig1 libgdk-pixbuf2.0-0 libglib2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libgbm1 xdg-utils wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Tell Selenium/SeleniumBase to use Chromium
ENV CHROME_BIN=/usr/bin/chromium \
    SB_CHROME_BINARY=/usr/bin/chromium \
    HEADLESS=1

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy the backend app
COPY . /app

# Use non-root user to avoid Chrome sandbox issues
RUN useradd -m appuser && \
    chown -R appuser:appuser /app
USER appuser

# Start the Gunicorn server; Railway will pass $PORT
CMD ["bash", "-lc", "exec gunicorn -w 1 -k gthread -b 0.0.0.0:${PORT:-8080} app:app"]



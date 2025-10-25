#!/bin/bash

# Fix chromedriver permissions (SeleniumBase downloads it without exec permission)
echo "Fixing chromedriver permissions..."
find /usr/local/lib/python*/site-packages/seleniumbase/drivers/ -name "chromedriver*" -exec chmod +x {} \; 2>/dev/null || true

# Start the application
echo "Starting application with xvfb and gunicorn..."
exec xvfb-run -a -s '-screen 0 1920x1080x24' gunicorn -w 1 -k gthread -b 0.0.0.0:$PORT app:app

FROM python:3.11-slim

# Install system deps for Playwright/Chromium + xvfb for headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg curl \
    libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 \
    libxkbcommon0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libwayland-client0 \
    libx11-xcb1 libxcb1 libxext6 libx11-6 \
    xvfb fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Install Playwright browsers
RUN playwright install --with-deps chromium

# Copy app code
COPY . .

# Render uses PORT env var
ENV PORT=10000
ENV DISPLAY=:99

EXPOSE 10000

# Start xvfb + gunicorn
CMD Xvfb :99 -screen 0 1280x720x24 -nolisten tcp & \
    gunicorn --bind 0.0.0.0:${PORT} --timeout 120 --workers 1 --threads 4 app:app

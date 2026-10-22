# Use the official Microsoft Playwright image with Ubuntu, Python, and Chromium pre-installed
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=10000

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure data and static directories exist
RUN mkdir -p /app/data /app/static

EXPOSE 10000

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "10000"]

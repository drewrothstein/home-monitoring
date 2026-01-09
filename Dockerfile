FROM python:3.14.2-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY home_monitor/ ./home_monitor/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default command - run HTTP server
# For one-off fetches, override with: python -m home_monitor.fetcher
CMD ["python", "-m", "home_monitor.server"]


# Use a lightweight Python 3.12 image.
FROM python:3.12-slim

# Prevent unnecessary .pyc files and enable immediate logs.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# All application paths are relative to /app.
WORKDIR /app

# Install dependencies separately for build-layer caching.
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, JSON fixtures and tests.
COPY backend/ ./backend/
COPY data/ ./data/
COPY tests/ ./tests/

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# The application listens on port 8000.
EXPOSE 8000

# Bind to all container interfaces.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11-slim

# Create venv outside app
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen 

# Copy application code
COPY . /app/ai_service

# Run the AI service worker
CMD ["python", "-m", "ai_service.worker"]


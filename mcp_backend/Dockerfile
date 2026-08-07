FROM python:3.13-slim

WORKDIR /app

# System deps: Node.js (pdf-parse), build tools for any wheels that need compiling
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    build-essential \
 && npm install -g pdf-parse \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install the project itself (pulls deps from pyproject.toml)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy the rest of the source
COPY . .

ENV PORT=8100
EXPOSE 8100

CMD ["python", "mcp_backend/main.py", "--host", "0.0.0.0", "--port", "8100"]

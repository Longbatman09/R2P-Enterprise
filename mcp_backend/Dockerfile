FROM python:3.13-slim

WORKDIR /app

# Install Node.js (needed by pdf_extractor.js)
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    && npm install -g pdf-parse \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY mcp_backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project (agent code, mcp_servers, etc.)
COPY . .

# Expose port
ENV PORT=8100
EXPOSE 8100

CMD ["python", "mcp_backend/main.py", "--host", "0.0.0.0", "--port", "8100"]

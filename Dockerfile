# ---- Frontend build stage ----
FROM node:20-slim AS ui-build
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ---- Backend runtime stage ----
FROM python:3.11-slim

# Install system dependencies (curl for collect/knesset and for uv)
RUN apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /bin/uv

# Set working directory
WORKDIR /app

# Copy the entire project
COPY . /app

# Bring in the frontend built in the ui-build stage (ui/dist is gitignored,
# so it isn't present in the plain `COPY . /app` above).
COPY --from=ui-build /app/ui/dist /app/ui/dist

# Install dependencies for the main project
RUN uv sync --frozen

# Ensure scripts are executable
RUN chmod +x /app/run_daily_pipeline.sh

# The default command will be overridden by Cloud Run (for web) and Cloud Run Jobs (for daily tasks)
# But we can set a sensible default, like running the web server
CMD ["uv", "run", "mkwork", "--host", "0.0.0.0", "--port", "8080"]

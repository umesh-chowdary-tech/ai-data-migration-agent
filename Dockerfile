# 1. Build the UI (so the image doesn't depend on the committed dist)
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. Run the agent
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY mock_api/ ./mock_api/
COPY scripts/ ./scripts/
COPY evals/ ./evals/
COPY data/ ./data/
COPY --from=ui /ui/dist ./frontend/dist

# Databases and uploaded files live here; mount a volume at /data to keep them across restarts.
ENV AGENT_DB=/data/agent.db TARGET_DB=/data/target.db RUNS_DIR=/data/runs PORT=8080
RUN mkdir -p /data
EXPOSE 8080
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]

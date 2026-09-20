# Hugging Face Spaces (Docker SDK). Spaces run the container as a non-root user on port 7860.
# 1. Build the UI
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. Run the agent
FROM python:3.12-slim
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user backend/ ./backend/
COPY --chown=user mock_api/ ./mock_api/
COPY --chown=user scripts/ ./scripts/
COPY --chown=user evals/ ./evals/
COPY --chown=user data/ ./data/
COPY --chown=user --from=ui /ui/dist ./frontend/dist
RUN mkdir -p var/runs

# Free Spaces have no persistent disk: runs and learned rules reset when the Space restarts. The sample data and
# its ground truth ship in the image, so a visitor can always start a fresh migration.
ENV AGENT_DB=/home/user/app/var/agent.db \
    TARGET_DB=/home/user/app/var/target.db \
    RUNS_DIR=/home/user/app/var/runs \
    PORT=7860 \
    MAX_RUNS_PER_HOUR=20
EXPOSE 7860
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]

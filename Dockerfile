# ── Stage 1: Build Next.js ──
FROM node:20-slim AS builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Production image (Node + Python) ──
FROM node:20-slim

# Install Python 3 + pip + system libs needed by OpenCV headless
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libglib2.0-0 libgl1-mesa-glx libsm6 libxext6 libxrender1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ──
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt

# ── Python backend files ──
COPY main.py extractor.py ./

# ── Next.js standalone output ──
COPY --from=builder /app/frontend/.next/standalone ./frontend/
COPY --from=builder /app/frontend/.next/static ./frontend/.next/static
COPY --from=builder /app/frontend/public ./frontend/public

# Env defaults
ENV NODE_ENV=production
ENV PYTHON_PATH=python3
ENV PROJECT_ROOT=/app
ENV PORT=3000

EXPOSE 3000

# Start Next.js standalone server
WORKDIR /app/frontend
CMD ["node", "server.js"]

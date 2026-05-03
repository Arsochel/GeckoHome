FROM python:3.11-slim

ARG MEDIAMTX_VERSION=v1.9.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# cloudflared
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}" && \
    chmod +x /usr/local/bin/cloudflared

# mediamtx
RUN ARCH=$(dpkg --print-architecture) && \
    MTX_ARCH=$([ "$ARCH" = "arm64" ] && echo "arm64v8" || echo "amd64") && \
    wget -qO /tmp/mediamtx.tar.gz \
      "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${MTX_ARCH}.tar.gz" && \
    tar -xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx && \
    rm /tmp/mediamtx.tar.gz

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "main.py"]

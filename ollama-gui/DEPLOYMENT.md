# Deployment Guide

## Docker Compose (Recommended)

The project includes a `compose.yml` that runs both the Ollama GUI and an Ollama server instance.

### Services

| Service      | Container       | Host Port | Container Port | Description            |
|--------------|-----------------|-----------|----------------|------------------------|
| ollama-gui   | ollama_gui      | 8002      | 80             | Nginx serving the Vue app |
| ollama       | ollama          | 11435     | 11434          | Ollama LLM server      |

### Starting Up

```bash
docker compose up -d
```

The GUI is available at **http://localhost:8002**.

### Rebuilding After Code Changes

```bash
docker compose build ollama-gui && docker compose up -d ollama-gui
```

This rebuilds only the GUI container and restarts it without affecting the Ollama container or its downloaded models.

### Stopping

```bash
docker compose down
```

### GPU Support

If you have an NVIDIA GPU, uncomment the `deploy.resources` block in `compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

### Pulling Models

```bash
docker exec -it ollama ollama pull llama3.2
docker exec -it ollama ollama pull deepseek-r1:7b
```

## Nginx Configuration

The file `nginx/default.conf` handles two responsibilities:

1. **Serves the static Vue build** from `/usr/share/nginx/html` with SPA fallback (`try_files $uri /index.html`).
2. **Proxies API requests** from `/api/` to the Ollama container at `http://ollama:11434/api/`.

Key proxy settings:
- `proxy_buffering off` -- Required for streaming responses to flow in real time.
- `proxy_read_timeout 3600` -- Long timeout so large model responses are not cut off.
- `proxy_request_buffering off` -- Allows large image uploads to pass through without buffering.

## Ollama Environment

The Ollama container is configured with:

```yaml
environment:
  - OLLAMA_ORIGINS=*
```

This allows cross-origin requests from the GUI container. In production behind a reverse proxy this is safe because the proxy handles the actual client-facing CORS.

## Model Storage

The compose file mounts the host model directory into the container:

```yaml
volumes:
  - /home/your-user/.ollama/models:/root/.ollama/models
```

This means models downloaded on the host or inside the container are shared. Adjust this path to match your host's home directory.

## Manual Deployment (Without Docker)

If you prefer to serve the app without Docker:

```bash
# Build the production bundle
npm run build

# Copy dist/ to your web server's document root
cp -r dist/* /var/www/ollama-gui/
```

Then configure your web server to:
1. Serve the files from that directory.
2. Return `index.html` for all non-file routes (SPA fallback).
3. Proxy `/api/` requests to your Ollama instance (default `http://localhost:11434/api/`).

## Production Deployment

Running via Docker Compose on an Ubuntu server. To deploy updates:

```bash
# SSH into your server
ssh your-user@your-server-ip

# Navigate to the project directory and pull changes
cd /path/to/ollama-gui
git pull

# Rebuild and restart
docker compose build ollama-gui && docker compose up -d ollama-gui
```

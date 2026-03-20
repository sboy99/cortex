# Cortex — Discord Daily Summary Bot

A production-ready Discord bot that aggregates messages from configured channels, summarizes them via local Ollama, and DMs you daily and weekly.

## Features

- **Daily summary** — Messages from configured channels summarized and sent via DM at a scheduled time
- **Weekly summary** — Last 7 days of daily summaries aggregated into a weekly digest
- **7-day retention** — Summaries stored in SQLite for weekly rollup; old data pruned automatically

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with a model (e.g. `ollama pull llama3.2`)
- Discord bot token and server access for the channels you want to monitor

## Setup

1. Create a [Discord application](https://discord.com/developers/applications) and bot
2. Invite the bot to your servers with "Read Message History" and "Send Messages" (for DMs)
3. Install Ollama and pull a model: `ollama pull llama3.2`
4. Copy `.env.example` to `.env` and fill in values:

   ```bash
   cp .env.example .env
   ```

5. Run with Docker:

   ```bash
   docker-compose up -d
   ```

   Or run locally:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or `.\\.venv\\Scripts\\activate` on Windows
   pip install -r requirements.txt
   python -m src.main
   ```

## Configuration

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token |
| `DISCORD_USER_ID` | Your Discord user ID (recipient of summaries) |
| `CHANNEL_IDS` | Comma-separated channel IDs to monitor |
| `OLLAMA_HOST` | Ollama API URL (use `http://host.docker.internal:11434` in Docker to reach host Ollama) |
| `OLLAMA_MODEL` | Model name (e.g. `llama3.2`, `mistral`) |
| `SUMMARY_UTC_HOUR` | Hour for daily summary (0–23) |
| `SUMMARY_UTC_MINUTE` | Minute for daily summary |
| `SUMMARY_WEEKLY_DAY` | Day of week for weekly summary (0=Mon, 6=Sun) |
| `SUMMARY_WEEKLY_UTC_HOUR` | Hour for weekly summary |
| `SUMMARY_WEEKLY_UTC_MINUTE` | Minute for weekly summary |

## Limitations

- Bot must be in the server and have Read Message History in each monitored channel
- Ollama must be reachable from the container (e.g. `host.docker.internal` on Mac/Windows)
- Only the last 500 messages per channel per run are collected

## Troubleshooting: "Failed to connect to Ollama" in Docker

If Cortex runs in Docker and cannot reach Ollama:

1. **Ollama must listen on all interfaces** — By default Ollama binds to `127.0.0.1`, which is unreachable from a container. On the host, start Ollama with:
   ```bash
   OLLAMA_HOST=0.0.0.0 ollama serve
   ```
   Or set `OLLAMA_HOST=0.0.0.0` in your shell before running `ollama serve`. If Ollama runs as a service, configure it to listen on `0.0.0.0`.

2. **Use the right `OLLAMA_HOST` in .env** — Inside the container, `localhost` refers to the container, not your machine. In `.env` set:
   ```
   OLLAMA_HOST=http://host.docker.internal:11434
   ```
   (Mac/Windows Docker Desktop). On Linux, `host.docker.internal` is not defined by default — add to `docker-compose.yml` under the service:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```

3. **Verify Ollama is reachable** — On the host: `curl http://localhost:11434/api/tags`. From inside the container:
   ```bash
   docker exec cortex curl -s http://host.docker.internal:11434/api/tags
   ```
   A JSON response means Ollama is reachable.

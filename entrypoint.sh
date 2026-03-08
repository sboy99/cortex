#!/bin/sh
set -e
mkdir -p /app/data
exec python -m src.main

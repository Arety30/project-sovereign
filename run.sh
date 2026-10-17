#!/bin/bash

# Exit immediately if any command fails
set -e

echo "=========================================================="
echo "          SOVEREIGN OFFLINE AGENTIC SYSTEM                "
echo "=========================================================="
echo ""

# Get project root directory
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$PROJECT_DIR"

# 1. Activate python virtual environment
if [ -d ".venv" ]; then
    echo "[+] Activating Python virtual environment..."
    source .venv/bin/activate
else
    echo "[-] Error: Virtual environment .venv not found. Run installation first."
    exit 1
fi

# 2. Check if Ollama is running
echo "[+] Checking local Ollama engine status..."
if curl -s http://localhost:11434/api/tags > /dev/null; then
    echo "[+] Ollama server detected on port 11434."
else
    echo "[!] Warning: Local Ollama server is not running or not listening on port 11434."
    echo "    Please run: 'ollama serve' in another terminal."
fi

# 3. Launch FastAPI backend and serve premium UI dashboard
echo "[+] Launching Sovereign Agent FastAPI Server on http://localhost:8000..."
echo "[+] You can open http://localhost:8000 in your web browser to interact with the agent."
echo ""
echo "Press Ctrl+C to stop the system."
echo "----------------------------------------------------------"

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000

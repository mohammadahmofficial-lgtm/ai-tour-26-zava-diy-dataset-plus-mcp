#!/bin/bash

# Ensure we are in the analytics_dashboard directory
cd "$(dirname "$0")"

echo "========================================="
echo " Starting Zava Demo Application"
echo "========================================="

echo "[1/3] Installing requirements..."
pip install -r requirements.txt > /dev/null 2>&1

echo "[2/3] Starting Agent API (Backend)..."
python backend/api.py &
BACKEND_PID=$!

echo "[3/3] Starting Web UI (Frontend)..."
python frontend/app.py &
FRONTEND_PID=$!

echo "========================================="
echo " Services are running!"
echo " 🌐 Web Interface: http://localhost:5000"
echo " ⚙️  Agent API:     http://localhost:8000"
echo "========================================="
echo "Press Ctrl+C to stop both services."

# Trap Ctrl+C to cleanly shut down both servers
trap "echo 'Shutting down...'; kill $BACKEND_PID $FRONTEND_PID; exit" INT TERM

# Wait indefinitely until interrupted
wait

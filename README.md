# ChatChat

ChatChat is a demo chat application that implements one-to-one chat, group chat and file sharing with application-level flow control and congestion control principles (TCP-like) for educational purposes.

This repository contains two main parts:
- backend/ — FastAPI WebSocket server + flow control skeleton
- frontend/ — React app placeholder

Quick start (development):

1. Backend

- Create a Python virtualenv and install deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

- Run the server:

```bash
uvicorn backend.app:app --reload --port 9009
```

2. Frontend (placeholder)

- Change to `frontend/`, run `npm install` and `npm start` (this is a minimal scaffold).

<!-- What's next
- Implement client-side flow/congestion control logic in the React app or provide a Python client demonstrating the algorithms.
- Harden file transfer, add MongoDB persistence for messages and files, and add authentication. -->


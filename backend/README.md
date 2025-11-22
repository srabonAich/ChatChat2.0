# Backend (FastAPI) for ChatChat

This backend provides a WebSocket endpoint at `/ws` that accepts `?name=USERNAME` and routes JSON messages between clients.

Notes:
- This is a demo scaffold. It does not implement full flow/congestion control yet — see `flow_control.py` for a simple primitive.
- For production, add authentication, TLS (wss), and persistent storage (MongoDB via Motor).

Run:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 9009
```


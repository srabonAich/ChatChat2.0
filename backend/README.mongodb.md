MongoDB setup (development)
===========================

This project stores assembled files and transfer metadata in MongoDB (GridFS for assembled files). The backend expects the environment variable `MONGODB_URI` to point to the MongoDB server.

Quick start using Docker Compose (recommended for development):

1. From the project root, start MongoDB:

```bash
docker compose up -d
```

This starts a `mongo` service on localhost:27017 and a persistent `mongo-data` volume.

2. Set the environment variable used by the backend (example):

```bash
export MONGODB_URI='mongodb://mongo:27017/chatchat'
# or if you prefer connecting to host MongoDB:
# export MONGODB_URI='mongodb://localhost:27017/chatchat'
```

3. (Optional) Verify connectivity from Python using Motor:

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 check_mongo.py
```

4. Start the backend (uvicorn):

```bash
cd backend
uvicorn app:app --reload --port 9009
```

Notes and next steps:
- For production, enable MongoDB authentication, use TLS, and set `MONGODB_URI` to include credentials.
- Consider adding a small retention job to delete `file_chunks` after assembly to avoid unbounded growth.

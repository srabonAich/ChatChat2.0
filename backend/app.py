from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
from typing import Dict, Set
import base64
import io
import os

# Motor for MongoDB
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

app = FastAPI()

# Allow the frontend (vite dev server) to call REST endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory structures for demo. Replace with persistent storage (MongoDB) for production.
clients: Dict[str, WebSocket] = {}       # name -> websocket
rooms: Dict[str, Set[str]] = {}          # room -> set(names)
lock = asyncio.Lock()

# MongoDB client (will be initialized in startup event)
mongo_client: AsyncIOMotorClient = None
db = None
gridfs_bucket: AsyncIOMotorGridFSBucket = None


@app.on_event("startup")
async def startup_event():
    global mongo_client, db, gridfs_bucket
    mongo_url = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017')
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = mongo_client['chatchat']
    gridfs_bucket = AsyncIOMotorGridFSBucket(db)


@app.on_event("shutdown")
async def shutdown_event():
    global mongo_client
    if mongo_client:
        mongo_client.close()

@app.get("/")
async def index():
    return HTMLResponse('<h3>ChatChat backend running. Connect via WebSocket at /ws?name=YOURNAME</h3>')

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # client must connect with ?name=...
    await websocket.accept()
    params = websocket.query_params
    name = params.get('name') or f'anon-{id(websocket)}'
    # register
    async with lock:
        if name in clients:
            await websocket.send_text(json.dumps({'type':'ERROR','why':'name taken'}))
            await websocket.close()
            return
        clients[name] = websocket
    # broadcast updated clients list to all connected
    async with lock:
        cl = list(clients.keys())
        for w in clients.values():
            try:
                await w.send_text(json.dumps({'type':'CLIENTS','clients': cl}))
            except Exception:
                pass
    print(f"[SERVER] {name} connected")
    await websocket.send_text(json.dumps({'type':'CONNECTED','you':name}))

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except Exception:
                await websocket.send_text(json.dumps({'type':'ERROR','why':'invalid json'}))
                continue
            # Log incoming frame for debugging (concise)
            try:
                mlog = {'from': msg.get('from'), 'type': msg.get('type'), 'to': msg.get('to'), 'room': msg.get('room')}
                if msg.get('type') in ('FILE_META','FILE_CHUNK'):
                    meta = msg.get('meta') or {}
                    mlog['transfer_id'] = meta.get('transfer_id') or msg.get('transfer_id')
                    if msg.get('type') == 'FILE_CHUNK':
                        mlog['chunk_index'] = msg.get('chunk_index')
                print('[SERVER LOG] recv', mlog)
            except Exception:
                print('[SERVER LOG] recv (failed to build log)')
            # basic routing
            mtype = msg.get('type')
            if mtype == 'JOIN':
                room = msg.get('room')
                if not room: continue
                async with lock:
                    rooms.setdefault(room, set()).add(name)
                await websocket.send_text(json.dumps({'type':'JOINED','room':room}))
            elif mtype == 'LEAVE':
                room = None
                async with lock:
                    for r, members in rooms.items():
                        if name in members:
                            members.discard(name)
                            room = r
                            break
                await websocket.send_text(json.dumps({'type':'LEFT','room':room}))
            elif mtype in ('MSG','FILE_META','FILE_CHUNK'):
                # either to a specific user or broadcast to a room
                # For file transfers we expect a transfer identifier to be present in FILE_META and FILE_CHUNK
                if mtype == 'FILE_META':
                    # persist transfer metadata
                    meta = msg.get('meta') or {}
                    transfer_id = meta.get('transfer_id') or msg.get('transfer_id')
                    if transfer_id and db is not None:
                        try:
                            await db.file_transfers.update_one(
                                {'transfer_id': transfer_id},
                                {'$set': {
                                    'transfer_id': transfer_id,
                                    'sender': msg.get('from'),
                                    'fname': meta.get('fname'),
                                    'size': meta.get('size'),
                                    'total_chunks': meta.get('total_chunks'),
                                    'status': 'in_progress'
                                }}, upsert=True)
                        except Exception as e:
                            # Log DB error but don't crash the websocket handler
                            print(f"[SERVER LOG] error persisting FILE_META for {transfer_id}: {e}")
                if mtype == 'FILE_CHUNK':
                    # allow transfer_id either at top-level or inside meta
                    transfer_id = msg.get('transfer_id') or (msg.get('meta') or {}).get('transfer_id')
                    if not transfer_id:
                        await websocket.send_text(json.dumps({'type':'ERROR','why':'missing transfer_id in FILE_CHUNK'}))
                        continue
                    # store chunk in DB for persistence
                    if db is not None:
                        try:
                            chunk_b64 = msg.get('payload') or ''
                            chunk_bytes = base64.b64decode(chunk_b64)
                            await db.file_chunks.insert_one({
                                'transfer_id': transfer_id,
                                'chunk_index': int(msg.get('chunk_index', 0)),
                                'data': chunk_bytes
                            })
                            # Inform sender that server received the chunk (debug helper)
                            try:
                                await websocket.send_text(json.dumps({'type':'SERVER_RECV_CHUNK','transfer_id': transfer_id, 'chunk_index': int(msg.get('chunk_index', 0))}))
                            except Exception:
                                pass
                            # check if assembly is complete
                            meta_doc = await db.file_transfers.find_one({'transfer_id': transfer_id})
                            total = meta_doc.get('total_chunks') if meta_doc else msg.get('total_chunks')
                            cnt = await db.file_chunks.count_documents({'transfer_id': transfer_id})
                            if total and cnt >= int(total):
                                # assemble
                                chunks_cursor = db.file_chunks.find({'transfer_id': transfer_id}).sort('chunk_index', 1)
                                assembled = bytearray()
                                async for ch in chunks_cursor:
                                    assembled.extend(ch['data'])
                                # store in GridFS
                                if gridfs_bucket is not None:
                                    stream = io.BytesIO(assembled)
                                    file_id = await gridfs_bucket.upload_from_stream(meta_doc.get('fname') if meta_doc else f'file_{transfer_id}', stream)
                                    # store gridfs id on transfer doc
                                    await db.file_transfers.update_one({'transfer_id': transfer_id}, {'$set': {'status':'complete', 'gridfs_id': file_id}})
                                else:
                                    # mark transfer complete even if GridFS unavailable
                                    await db.file_transfers.update_one({'transfer_id': transfer_id}, {'$set': {'status':'complete'}})
                                # remove chunk documents now that file is assembled
                                try:
                                    await db.file_chunks.delete_many({'transfer_id': transfer_id})
                                except Exception:
                                    pass
                                # notify connected clients that file is ready
                                try:
                                    notify = {'type':'FILE_READY','transfer_id': transfer_id, 'fname': meta_doc.get('fname') if meta_doc else f'file_{transfer_id}', 'sender': msg.get('from')}
                                    async with lock:
                                        for w in clients.values():
                                            try:
                                                await w.send_text(json.dumps(notify))
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                        except Exception as e:
                            print('[SERVER] error persisting chunk:', e)

                dest = msg.get('to')
                if dest:
                    async with lock:
                        target = clients.get(dest)
                    if target:
                        print(f"[SERVER LOG] forward to user {dest} (from {name}) type={mtype}")
                        await target.send_text(json.dumps(msg))
                    else:
                        print(f"[SERVER LOG] cannot forward to user {dest} (not connected)")
                        await websocket.send_text(json.dumps({'type':'ERROR','why':'no such user'}))
                else:
                    room = msg.get('room')
                    if not room:
                        # try to determine user's current room (first one found)
                        async with lock:
                            user_room = None
                            for r, members in rooms.items():
                                if name in members:
                                    user_room = r; break
                        room = user_room
                    if not room:
                        await websocket.send_text(json.dumps({'type':'ERROR','why':'not in room'}))
                        continue
                    async with lock:
                        members = list(rooms.get(room, []))
                    print(f"[SERVER LOG] broadcast to room {room} members={members} from={name} type={mtype}")
                    for member in members:
                        if member == name: continue
                        async with lock:
                            t = clients.get(member)
                        if t:
                            await t.send_text(json.dumps(msg))
            # ACK handled above
            elif mtype == 'ACK':
                # ACKs should be routed to a specific recipient ('to')
                dest = msg.get('to')
                if dest:
                    async with lock:
                        target = clients.get(dest)
                    if target:
                        print(f"[SERVER LOG] forward ACK from {name} to {dest} transfer_id={msg.get('transfer_id')} ack={msg.get('ack')}")
                        await target.send_text(json.dumps(msg))
                    else:
                        print(f"[SERVER LOG] ACK target {dest} not connected")
                        await websocket.send_text(json.dumps({'type':'ERROR','why':'no such user for ACK'}))
                else:
                    # no destination: can't route, inform sender
                    await websocket.send_text(json.dumps({'type':'ERROR','why':'ACK missing to field'}))
            else:
                # unknown type: reply error
                await websocket.send_text(json.dumps({'type':'ERROR','why':'unknown type'}))

    except WebSocketDisconnect:
        pass
    finally:
        # cleanup
        async with lock:
            clients.pop(name, None)
            for r in list(rooms.keys()):
                rooms[r].discard(name)
                if len(rooms[r]) == 0:
                    del rooms[r]
        # broadcast updated clients list after disconnect
        async with lock:
            cl = list(clients.keys())
            for w in clients.values():
                try:
                    await w.send_text(json.dumps({'type':'CLIENTS','clients': cl}))
                except Exception:
                    pass
        print(f"[SERVER] {name} disconnected")


@app.get('/clients')
async def list_clients():
    async with lock:
        names = list(clients.keys())
    return JSONResponse({'clients': names})


from fastapi.responses import StreamingResponse


@app.get('/file/{transfer_id}')
async def get_file(transfer_id: str):
    if db is None or gridfs_bucket is None:
        return JSONResponse({'error':'storage not configured'}, status_code=500)
    doc = await db.file_transfers.find_one({'transfer_id': transfer_id})
    if not doc:
        return JSONResponse({'error':'not found'}, status_code=404)
    gridfs_id = doc.get('gridfs_id')
    if not gridfs_id:
        return JSONResponse({'error':'file not yet assembled'}, status_code=404)

    # stream from GridFS
    async def iterfile():
        stream = await gridfs_bucket.open_download_stream(gridfs_id)
        chunk = await stream.read(8192)
        while chunk:
            yield chunk
            chunk = await stream.read(8192)
        await stream.close()

    filename = doc.get('fname') or f'file_{transfer_id}'
    return StreamingResponse(iterfile(), media_type='application/octet-stream', headers={'Content-Disposition': f'attachment; filename="{filename}"'})

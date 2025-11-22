#!/usr/bin/env python3
"""
client_tcp.py
A cleaned-up TCP demo client for ChatChat (length-prefixed JSON framing).
Usage: python3 client_tcp.py --name Alice

This is based on the earlier client implementation and includes robust framing (recvall),
JSON error handling, and the existing Sender/Receiver demo logic.
"""
import socket
import threading
import json
import struct
import argparse
import time
import hashlib
import os
import base64

HOST = '127.0.0.1'
PORT = 9009

# --- Hardcoded parameters (easy to change for demos) ---
MSS = 512                      # bytes per segment payload
INIT_CWND = 1 * MSS            # initial congestion window (bytes)
INIT_SSTHRESH = 8 * MSS        # slow start threshold
RETRANSMIT_TIMEOUT = 1.0       # seconds
RECV_RWND = 32 * MSS           # receiver window advertise
MAX_SEQ = 2**31

# --- helpers: message framing ---
def send_msg(conn, obj):
    raw = json.dumps(obj, separators=(',',':')).encode('utf-8')
    try:
        conn.sendall(struct.pack('!I', len(raw)) + raw)
        return True
    except Exception as e:
        print('[CLIENT] send_msg failed:', e)
        try:
            conn.close()
        except:
            pass
        return False


def recv_msg(conn):
    def _recvall(n):
        buf = b''
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    hdr = _recvall(4)
    if not hdr:
        return None
    try:
        (n,) = struct.unpack('!I', hdr)
    except Exception:
        return None
    data = _recvall(n)
    if data is None:
        return None
    try:
        return json.loads(data.decode('utf-8'))
    except json.JSONDecodeError:
        print('[CLIENT] malformed JSON from server')
        return None

# --- Simple XOR-with-sha256 keystream encryption (educational only) ---
def derive_key_bytes(secret: bytes, length: int):
    out = b''
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(secret + counter.to_bytes(8,'big')).digest()
        counter += 1
    return out[:length]


def encrypt_bytes(data: bytes, key_secret: bytes):
    out = bytearray(len(data))
    block_size = 32
    for i in range(0, len(data), block_size):
        counter = (i // block_size).to_bytes(8,'big')
        ks = hashlib.sha256(key_secret + counter).digest()
        chunk = data[i:i+block_size]
        for j, b in enumerate(chunk):
            out[i+j] = b ^ ks[j]
    return bytes(out)


def decrypt_bytes(data: bytes, key_secret: bytes):
    return encrypt_bytes(data, key_secret)

# --- Sender side: manages send buffer, cwnd, ssthresh, retransmit, etc. ---
class Sender:
    def __init__(self, conn, myname):
        self.conn = conn
        self.myname = myname
        self.next_seq = 1
        self.send_base = 1
        self.buffer = {}    # seq -> dict(segment)
        self.buffer_lock = threading.Lock()

        # congestion variables (bytes)
        self.cwnd = INIT_CWND
        self.ssthresh = INIT_SSTHRESH

        self.dup_acks = {}  # ack -> count
        self.timer = None
        self.timer_lock = threading.Lock()

        # advertised receiver window (we will assume a default and update on ACKs)
        self.rwnd = RECV_RWND

        # start thread to listen for local send requests (user input)
        threading.Thread(target=self._user_input_loop, daemon=True).start()
        # thread to manage retransmit timers
        threading.Thread(target=self._retransmit_manager, daemon=True).start()

    def send_chat_message(self, to=None, room=None, text=''):
        data = text.encode('utf-8')
        self._enqueue_and_try_send(payload_type='MSG', to=to, room=room, payload=data)

    def _enqueue_and_try_send(self, payload_type, to, room, payload: bytes, meta=None):
        with self.buffer_lock:
            i = 0
            while i < len(payload):
                seg = payload[i:i+MSS]
                seq = self.next_seq
                self.buffer[seq] = {'payload':seg, 'meta':meta, 'type':payload_type, 'to':to, 'room':room, 'sent':False, 'sent_time':None}
                self.next_seq += len(seg) or 1
                i += len(seg)
        self._try_send()

    def _try_send(self):
        with self.buffer_lock:
            outstanding = (self.next_seq - self.send_base)
            allowed = int(min(self.cwnd, self.rwnd)) - outstanding
            if allowed <= 0:
                self._debug_print("Window full — cannot send now. outstanding=%d cwnd=%d rwnd=%d" % (outstanding, self.cwnd, self.rwnd))
                return
            seqs = sorted(self.buffer.keys())
            for seq in seqs:
                if allowed <= 0:
                    break
                seg = self.buffer[seq]
                if seg['sent'] is False:
                    to_send = seg['payload']
                    msg = {
                        'type': seg['type'],
                        'from': self.myname,
                        'to': seg['to'],
                        'room': seg['room'],
                        'seq': seq,
                        'payload': base64.b64encode(to_send).decode('ascii'),
                    }
                    ok = send_msg(self.conn, msg)
                    if not ok:
                        return
                    seg['sent'] = True
                    seg['sent_time'] = time.time()
                    self._debug_print(f"SENT seq={seq} len={len(to_send)} cwnd={self.cwnd} ssthresh={self.ssthresh} outstanding={outstanding}")
                    allowed -= len(to_send)
            with self.timer_lock:
                if self.timer is None or not self.timer.is_alive():
                    self.timer = threading.Thread(target=self._start_timer, daemon=True)
                    self.timer.start()

    def _start_timer(self):
        time.sleep(RETRANSMIT_TIMEOUT)
        with self.buffer_lock:
            if self.send_base >= self.next_seq:
                return
            seq = self.send_base
            if seq not in self.buffer:
                return
            print(f"[SENDER] Timeout for seq={seq} -> retransmit and reduce cwnd")
            self.ssthresh = max(int(self.cwnd // 2), MSS)
            self.cwnd = MSS
            self.buffer[seq]['sent'] = False
            self._try_send()

    def _retransmit_manager(self):
        while True:
            time.sleep(0.1)
            self._try_send()

    def handle_ack(self, ack, adv_rwnd=None):
        with self.buffer_lock:
            if adv_rwnd is not None:
                self.rwnd = adv_rwnd
            if ack > self.send_base:
                self._debug_print(f"ACK {ack} (new). old send_base={self.send_base}")
                to_delete = []
                for seq in list(self.buffer.keys()):
                    if seq < ack:
                        to_delete.append(seq)
                for seq in to_delete:
                    del self.buffer[seq]
                self.send_base = ack
                if self.cwnd < self.ssthresh:
                    self.cwnd += MSS
                else:
                    self.cwnd += max(1, int(MSS * (MSS / max(1,self.cwnd))))
                self.dup_acks.clear()
                self._debug_print(f"After ACK: cwnd={self.cwnd} ssthresh={self.ssthresh}")
            elif ack == self.send_base:
                self.dup_acks[ack] = self.dup_acks.get(ack, 0) + 1
                cnt = self.dup_acks[ack]
                self._debug_print(f"Duplicate ACK {ack} (count {cnt})")
                if cnt >= 3:
                    self.ssthresh = max(int(self.cwnd // 2), MSS)
                    self.cwnd = self.ssthresh + 3*MSS
                    if self.send_base in self.buffer:
                        self.buffer[self.send_base]['sent'] = False
                    self._debug_print(f"Fast retransmit triggered: cwnd={self.cwnd} ssthresh={self.ssthresh}")

    def _user_input_loop(self):
        print("Commands:\n  /msg <user> <text>\n  /room <room> <text>\n  /join <room>\n  /leave\n  /sendfile <user|room> <file_path>\n  /quit\n")
        while True:
            try:
                line = input('> ')
            except EOFError:
                break
            if not line: continue
            parts = line.split(' ', 2)
            cmd = parts[0]
            if cmd == '/msg' and len(parts) >= 3:
                to = parts[1]; text = parts[2]
                self.send_chat_message(to=to, text=text)
            elif cmd == '/room' and len(parts) >= 3:
                room = parts[1]; text = parts[2]
                self.send_chat_message(room=room, text=text)
            elif cmd == '/join' and len(parts) >= 2:
                room = parts[1]
                send_msg(self.conn, {'type':'JOIN','from':self.myname,'room':room})
            elif cmd == '/leave':
                send_msg(self.conn, {'type':'LEAVE','from':self.myname})
            elif cmd == '/sendfile' and len(parts) >= 3:
                target = parts[1]; path = parts[2]
                is_room = False
                if target.startswith('#'):
                    is_room = True; room = target.lstrip('#')
                    to = None
                else:
                    to = target; room = None
                self.send_file(path, to=to, room=room)
            elif cmd == '/quit':
                print("Quitting...")
                os._exit(0)
            else:
                print("Unknown or malformed command.")

    def send_file(self, path, to=None, room=None):
        if not os.path.exists(path):
            print("No such file:", path); return
        filesize = os.path.getsize(path)
        fname = os.path.basename(path)
        key_secret = os.urandom(32)
        meta = {'fname':fname, 'size':filesize, 'key': base64.b64encode(key_secret).decode('ascii')}
        send_msg(self.conn, {'type':'FILE_META','from':self.myname,'to':to,'room':room,'meta':meta})
        print(f"[SENDER] Sending encrypted file '{fname}' size={filesize} bytes")
        with open(path,'rb') as f:
            counter = 0
            while True:
                chunk = f.read(MSS * 4)
                if not chunk: break
                enc = encrypt_bytes(chunk, key_secret)
                self._enqueue_and_try_send(payload_type='FILE_CHUNK', to=to, room=room, payload=enc, meta={'chunk_id':counter})
                counter += 1
        print("[SENDER] File queued for send.")

    def _debug_print(self, s):
        print(f"[SENDER-{self.myname}] {s}")

# --- Receiver: processes incoming app segments and sends ACKs (advertises rwnd) ---
class Receiver:
    def __init__(self, conn, myname, sender: Sender):
        self.conn = conn
        self.myname = myname
        self.expected_seq = 1
        self.buffer = {}  # out-of-order seq -> payload
        self.lock = threading.Lock()
        self.sender = sender  # to send ACKs back to server
        self.files = {}

    def process_segment(self, msg):
        seq = msg.get('seq')
        payload_b64 = msg.get('payload') or ''
        payload = base64.b64decode(payload_b64)
        ty = msg.get('type')
        frm = msg.get('from')
        with self.lock:
            if seq == self.expected_seq:
                self._deliver_payload(ty, frm, payload, msg.get('meta'))
                self.expected_seq += len(payload) or 1
                while self.expected_seq in self.buffer:
                    p, t, fmeta = self.buffer.pop(self.expected_seq)
                    self._deliver_payload(t, frm, p, fmeta)
                    self.expected_seq += len(p) or 1
            elif seq > self.expected_seq:
                self.buffer[seq] = (payload, ty, msg.get('meta'))
                print(f"[RECV-{self.myname}] OUT-OF-ORDER seq={seq} expected={self.expected_seq}")
            else:
                print(f"[RECV-{self.myname}] DUP/OLD seq={seq} < expected={self.expected_seq}")
            ack_msg = {'type':'ACK','from':self.myname,'ack':self.expected_seq,'rwnd':RECV_RWND}
            send_msg(self.conn, ack_msg)
            try:
                self.sender.handle_ack(self.expected_seq, adv_rwnd=RECV_RWND)
            except Exception:
                pass

    def _deliver_payload(self, ty, frm, payload, meta):
        if ty == 'MSG':
            text = payload.decode('utf-8', errors='replace')
            print(f"[RECV-{self.myname}] MSG from={frm}: {text}")
        elif ty == 'FILE_CHUNK':
            fid = frm
            rec = self.files.setdefault(fid, {'chunks':[], 'meta':None})
            rec['chunks'].append(payload)
            print(f"[RECV-{self.myname}] Received file chunk len={len(payload)} from={frm}")
        elif ty == 'FILE_META':
            metaobj = meta
            print(f"[RECV-{self.myname}] FILE_META: {metaobj}")
        else:
            print(f"[RECV-{self.myname}] Unknown payload type {ty}")

# --- Main client logic: connects, spawns handler threads ---
def run_client(name):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    send_msg(sock, {'type':'CONNECT','from':name})
    resp = recv_msg(sock)
    if resp and resp.get('type') == 'CONNECTED':
        print(f"Connected as {name}")
    else:
        print("Failed to connect:", resp); return

    sender = Sender(sock, name)
    receiver = Receiver(sock, name, sender)

    def recv_loop():
        while True:
            m = recv_msg(sock)
            if m is None:
                print("Server closed or bad message")
                os._exit(0)
            mtype = m.get('type')
            if mtype in ('MSG','FILE_CHUNK'):
                receiver.process_segment(m)
            elif mtype == 'FILE_META':
                meta = m.get('meta')
                frm = m.get('from')
                key_b64 = meta.get('key')
                key = base64.b64decode(key_b64)
                fname = f"recv_from_{frm}_" + meta.get('fname')
                rec = receiver.files.get(frm)
                if not rec:
                    rec = {'chunks':[], 'meta':None}
                rec['meta'] = meta
                enc = b''.join(rec['chunks'])
                dec = decrypt_bytes(enc, key)
                with open(fname, 'wb') as fout:
                    fout.write(dec)
                print(f"[CLIENT] Wrote decrypted file to {fname} (size {len(dec)})")
                receiver.files.pop(frm, None)
            elif mtype == 'ACK':
                # server-echoed ACKs could arrive; but receiver also notifies sender locally
                pass
            elif mtype == 'JOINED':
                print(f"Joined room {m.get('room')}")
            elif mtype == 'LEFT':
                print(f"Left room {m.get('room')}")
            elif mtype == 'ERROR':
                print("Error from server:", m.get('why'))
            else:
                print("Server message:", m)

    threading.Thread(target=recv_loop, daemon=True).start()

    while True:
        time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    run_client(args.name)

"""
Shared framing helpers for length-prefixed JSON messages.
Format: 4-byte big-endian length header followed by UTF-8 JSON bytes.
"""
import json
import struct


def send_msg(conn, obj):
    raw = json.dumps(obj, separators=(',',':')).encode('utf-8')
    try:
        conn.sendall(struct.pack('!I', len(raw)) + raw)
        return True
    except Exception as e:
        # connection likely closed / broken pipe
        try:
            conn.close()
        except Exception:
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
        return None

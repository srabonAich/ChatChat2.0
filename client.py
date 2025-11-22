# # client.py
# # ChatChat client with application-level TCP simulation (flow + congestion control)
# # Use: python client.py --name Alice
# import socket
# import threading
# import json
# import struct
# import argparse
# import time
# import hashlib
# import os
# import base64

# HOST = '127.0.0.1'
# PORT = 9009

# # --- Hardcoded parameters (easy to change for demos) ---
# MSS = 512                      # bytes per segment payload
# INIT_CWND = 1 * MSS            # initial congestion window (bytes)
# INIT_SSTHRESH = 8 * MSS        # slow start threshold
# RETRANSMIT_TIMEOUT = 1.0       # seconds
# RECV_RWND = 32 * MSS           # receiver window advertise
# MAX_SEQ = 2**31

# # --- helpers: message framing ---
# def send_msg(conn, obj):
#     raw = json.dumps(obj, separators=(',',':')).encode('utf-8')
#     conn.sendall(struct.pack('!I', len(raw)) + raw)

# def recv_msg(conn):
#     hdr = conn.recv(4)
#     if not hdr:
#         return None
#     (n,) = struct.unpack('!I', hdr)
#     data = b''
#     while len(data) < n:
#         chunk = conn.recv(n - len(data))
#         if not chunk:
#             return None
#         data += chunk
#     return json.loads(data.decode('utf-8'))

# # --- Simple XOR-with-sha256 keystream encryption (educational only) ---
# def derive_key_bytes(secret: bytes, length: int):
#     # derive length bytes deterministically using repeated SHA256
#     out = b''
#     counter = 0
#     while len(out) < length:
#         out += hashlib.sha256(secret + counter.to_bytes(8,'big')).digest()
#         counter += 1
#     return out[:length]

# def encrypt_bytes(data: bytes, key_secret: bytes):
#     # CTR-like scheme: keystream blocks are SHA256(key||counter)
#     out = bytearray(len(data))
#     block_size = 32
#     for i in range(0, len(data), block_size):
#         counter = (i // block_size).to_bytes(8,'big')
#         ks = hashlib.sha256(key_secret + counter).digest()
#         chunk = data[i:i+block_size]
#         for j, b in enumerate(chunk):
#             out[i+j] = b ^ ks[j]
#     return bytes(out)

# def decrypt_bytes(data: bytes, key_secret: bytes):
#     # symmetric
#     return encrypt_bytes(data, key_secret)

# # --- Sender side: manages send buffer, cwnd, ssthresh, retransmit, etc. ---
# class Sender:
#     def __init__(self, conn, myname):
#         self.conn = conn
#         self.myname = myname
#         self.next_seq = 1
#         self.send_base = 1
#         self.buffer = {}    # seq -> (timestamp, bytes payload, meta)
#         self.buffer_lock = threading.Lock()

#         # congestion variables (bytes)
#         self.cwnd = INIT_CWND
#         self.ssthresh = INIT_SSTHRESH

#         self.dup_acks = {}  # ack -> count
#         self.timer = None
#         self.timer_lock = threading.Lock()

#         # advertised receiver window (we will assume a default and update on ACKs)
#         self.rwnd = RECV_RWND

#         # start thread to listen for local send requests (user input)
#         threading.Thread(target=self._user_input_loop, daemon=True).start()
#         # thread to manage retransmit timers
#         threading.Thread(target=self._retransmit_manager, daemon=True).start()

#     def send_chat_message(self, to=None, room=None, text=''):
#         # chunk text and put into buffer as application segments
#         data = text.encode('utf-8')
#         self._enqueue_and_try_send(payload_type='MSG', to=to, room=room, payload=data)

#     def _enqueue_and_try_send(self, payload_type, to, room, payload: bytes, meta=None):
#         with self.buffer_lock:
#             # split into MSS-sized segments
#             i = 0
#             while i < len(payload):
#                 seg = payload[i:i+MSS]
#                 seq = self.next_seq
#                 self.buffer[seq] = {'payload':seg, 'meta':meta, 'type':payload_type, 'to':to, 'room':room, 'sent':False, 'sent_time':None}
#                 self.next_seq += len(seg) or 1
#                 i += len(seg)
#         self._try_send()

#     def _try_send(self):
#         # send as many bytes as allowed by min(cwnd, rwnd) - (next_seq - send_base outstanding)
#         with self.buffer_lock:
#             outstanding = (self.next_seq - self.send_base)
#             allowed = int(min(self.cwnd, self.rwnd)) - outstanding
#             if allowed <= 0:
#                 # nothing allowed now
#                 self._debug_print("Window full — cannot send now. outstanding=%d cwnd=%d rwnd=%d" % (outstanding, self.cwnd, self.rwnd))
#                 return
#             # iterate through buffer in seq order
#             seqs = sorted(self.buffer.keys())
#             for seq in seqs:
#                 if allowed <= 0:
#                     break
#                 seg = self.buffer[seq]
#                 if seg['sent'] is False:
#                     to_send = seg['payload']
#                     msg = {
#                         'type': seg['type'],
#                         'from': self.myname,
#                         'to': seg['to'],
#                         'room': seg['room'],
#                         'seq': seq,
#                         'payload': base64.b64encode(to_send).decode('ascii'),
#                     }
#                     try:
#                         send_msg(self.conn, msg)
#                         seg['sent'] = True
#                         seg['sent_time'] = time.time()
#                         self._debug_print(f"SENT seq={seq} len={len(to_send)} cwnd={self.cwnd} ssthresh={self.ssthresh} outstanding={outstanding}")
#                     except Exception as e:
#                         print("Send failed:", e)
#                         return
#                     allowed -= len(to_send)
#             # start timer if not running
#             with self.timer_lock:
#                 if self.timer is None or not self.timer.is_alive():
#                     self.timer = threading.Thread(target=self._start_timer, daemon=True)
#                     self.timer.start()

#     def _start_timer(self):
#         # when timer expires, retransmit oldest unacked
#         time.sleep(RETRANSMIT_TIMEOUT)
#         with self.buffer_lock:
#             if self.send_base >= self.next_seq:
#                 return  # nothing outstanding
#             # find oldest unacked (send_base)
#             seq = self.send_base
#             if seq not in self.buffer:
#                 return
#             print(f"[SENDER] Timeout for seq={seq} -> retransmit and reduce cwnd")
#             # update congestion control: on timeout, ssthresh = cwnd/2, cwnd = MSS
#             self.ssthresh = max(int(self.cwnd // 2), MSS)
#             self.cwnd = MSS
#             # mark the segment as unsent so retransmit will occur
#             self.buffer[seq]['sent'] = False
#             # try sending again
#             self._try_send()

#     def _retransmit_manager(self):
#         while True:
#             time.sleep(0.1)
#             # this keeps trying to fill the window if cwnd expanded
#             self._try_send()

#     def handle_ack(self, ack, adv_rwnd=None):
#         with self.buffer_lock:
#             # ack is cumulative: everything < ack is received
#             if adv_rwnd is not None:
#                 self.rwnd = adv_rwnd
#             if ack > self.send_base:
#                 # new ack
#                 self._debug_print(f"ACK {ack} (new). old send_base={self.send_base}")
#                 # remove acked segments
#                 to_delete = []
#                 for seq in list(self.buffer.keys()):
#                     # if seq < ack then acked
#                     if seq < ack:
#                         to_delete.append(seq)
#                 for seq in to_delete:
#                     del self.buffer[seq]
#                 # update send_base
#                 self.send_base = ack
#                 # congestion control: if in slow start or congestion avoidance
#                 # simple model: if cwnd < ssthresh -> slow start (cwnd += MSS per ACK)
#                 # else cwnd += MSS * (MSS / cwnd) approximated by cwnd += MSS*(MSS/cwnd)
#                 if self.cwnd < self.ssthresh:
#                     # slow start: exponential (approx per ack increase by MSS)
#                     self.cwnd += MSS
#                 else:
#                     # linear increase
#                     self.cwnd += max(1, int(MSS * (MSS / max(1,self.cwnd))))
#                 # reset dup_acks
#                 self.dup_acks.clear()
#                 self._debug_print(f"After ACK: cwnd={self.cwnd} ssthresh={self.ssthresh}")
#             elif ack == self.send_base:
#                 # duplicate ACK
#                 self.dup_acks[ack] = self.dup_acks.get(ack, 0) + 1
#                 cnt = self.dup_acks[ack]
#                 self._debug_print(f"Duplicate ACK {ack} (count {cnt})")
#                 if cnt >= 3:
#                     # fast retransmit: set ssthresh, cwnd = ssthresh + 3*MSS, retransmit segment
#                     self.ssthresh = max(int(self.cwnd // 2), MSS)
#                     self.cwnd = self.ssthresh + 3*MSS
#                     # retransmit the earliest segment (send_base)
#                     if self.send_base in self.buffer:
#                         self.buffer[self.send_base]['sent'] = False
#                     self._debug_print(f"Fast retransmit triggered: cwnd={self.cwnd} ssthresh={self.ssthresh}")

#     # -- user input loop for demo (type commands) --
#     def _user_input_loop(self):
#         # Commands:
#         # /msg <user> <text>
#         # /room <room> <text>
#         # /join <room>
#         # /sendfile <user|room> <path>
#         print("Commands:\n  /msg <user> <text>\n  /room <room> <text>\n  /join <room>\n  /leave\n  /sendfile <user|room> <file_path>\n  /quit\n")
#         while True:
#             try:
#                 line = input('> ')
#             except EOFError:
#                 break
#             if not line: continue
#             parts = line.split(' ', 2)
#             cmd = parts[0]
#             if cmd == '/msg' and len(parts) >= 3:
#                 to = parts[1]; text = parts[2]
#                 self.send_chat_message(to=to, text=text)
#             elif cmd == '/room' and len(parts) >= 3:
#                 room = parts[1]; text = parts[2]
#                 self.send_chat_message(room=room, text=text)
#             elif cmd == '/join' and len(parts) >= 2:
#                 room = parts[1]
#                 send_msg(self.conn, {'type':'JOIN','from':self.myname,'room':room})
#             elif cmd == '/leave':
#                 send_msg(self.conn, {'type':'LEAVE','from':self.myname})
#             elif cmd == '/sendfile' and len(parts) >= 3:
#                 target = parts[1]; path = parts[2]
#                 is_room = False
#                 if target.startswith('#'):
#                     is_room = True; room = target.lstrip('#')
#                     to = None
#                 else:
#                     to = target; room = None
#                 self.send_file(path, to=to, room=room)
#             elif cmd == '/quit':
#                 print("Quitting...")
#                 os._exit(0)
#             else:
#                 print("Unknown or malformed command.")

#     # --- file send with encryption and chunking ---
#     def send_file(self, path, to=None, room=None):
#         if not os.path.exists(path):
#             print("No such file:", path); return
#         filesize = os.path.getsize(path)
#         fname = os.path.basename(path)
#         # derive a random key secret for this file transfer and send meta
#         key_secret = os.urandom(32)
#         meta = {'fname':fname, 'size':filesize, 'key': base64.b64encode(key_secret).decode('ascii')}
#         send_msg(self.conn, {'type':'FILE_META','from':self.myname,'to':to,'room':room,'meta':meta})
#         print(f"[SENDER] Sending encrypted file '{fname}' size={filesize} bytes")
#         with open(path,'rb') as f:
#             counter = 0
#             while True:
#                 chunk = f.read(MSS * 4)  # make file chunks larger than MSS; we will split further
#                 if not chunk: break
#                 enc = encrypt_bytes(chunk, key_secret)  # encrypt chunk
#                 # now split enc into MSS-sized application segments and enqueue
#                 self._enqueue_and_try_send(payload_type='FILE_CHUNK', to=to, room=room, payload=enc, meta={'chunk_id':counter})
#                 counter += 1
#         print("[SENDER] File queued for send.")

#     def _debug_print(self, s):
#         print(f"[SENDER-{self.myname}] {s}")

# # --- Receiver: processes incoming app segments and sends ACKs (advertises rwnd) ---
# class Receiver:
#     def __init__(self, conn, myname, sender: Sender):
#         self.conn = conn
#         self.myname = myname
#         self.expected_seq = 1
#         self.buffer = {}  # out-of-order seq -> payload
#         self.lock = threading.Lock()
#         self.sender = sender  # to send ACKs back to server
#         # storage for file assembling: file_id -> bytearray + meta
#         self.files = {}

#     def process_segment(self, msg):
#         seq = msg.get('seq')
#         payload_b64 = msg.get('payload') or ''
#         payload = base64.b64decode(payload_b64)
#         ty = msg.get('type')
#         frm = msg.get('from')
#         # For simplicity: whenever we receive a segment, we ACK cumulative next expected seq (expected_seq)
#         with self.lock:
#             if seq == self.expected_seq:
#                 # in-order
#                 self._deliver_payload(ty, frm, payload, msg.get('meta'))
#                 self.expected_seq += len(payload) or 1
#                 # check buffer for next chunks
#                 while self.expected_seq in self.buffer:
#                     p, t, fmeta = self.buffer.pop(self.expected_seq)
#                     self._deliver_payload(t, frm, p, fmeta)
#                     self.expected_seq += len(p) or 1
#             elif seq > self.expected_seq:
#                 # out-of-order: buffer it
#                 self.buffer[seq] = (payload, ty, msg.get('meta'))
#                 print(f"[RECV-{self.myname}] OUT-OF-ORDER seq={seq} expected={self.expected_seq}")
#             else:
#                 # duplicate/old: ignore
#                 print(f"[RECV-{self.myname}] DUP/OLD seq={seq} < expected={self.expected_seq}")
#             # send ACK advertising expected_seq and rwnd
#             ack_msg = {'type':'ACK','from':self.myname,'ack':self.expected_seq,'rwnd':RECV_RWND}
#             send_msg(self.conn, ack_msg)
#             # also notify Sender instance (so it can change cwnd)
#             try:
#                 self.sender.handle_ack(self.expected_seq, adv_rwnd=RECV_RWND)
#             except Exception:
#                 pass

#     def _deliver_payload(self, ty, frm, payload, meta):
#         if ty == 'MSG':
#             text = payload.decode('utf-8', errors='replace')
#             print(f"[RECV-{self.myname}] MSG from={frm}: {text}")
#         elif ty == 'FILE_CHUNK':
#             # assemble based on from+meta+file meta (this demo uses a simple approach)
#             # meta may have chunk_id. For simplicity assemble to filename "<from>_incomplete"
#             key_b64 = None
#             # We need to get file key via FILE_META; but for demo we'll store chunks and decrypt after FILE_META arrives
#             fid = frm  # naive: one file per sender at a time
#             rec = self.files.setdefault(fid, {'chunks':[], 'meta':None})
#             rec['chunks'].append(payload)
#             print(f"[RECV-{self.myname}] Received file chunk len={len(payload)} from={frm}")
#             # if meta indicated final chunk we would assemble; here, wait for FILE_META to know filename and key
#         elif ty == 'FILE_META':
#             metaobj = msg.get('meta')
#             # This branch won't be reached here because FILE_META handled as MSG by server -> but ensure logic
#             print(f"[RECV-{self.myname}] FILE_META: {metaobj}")
#         else:
#             print(f"[RECV-{self.myname}] Unknown payload type {ty}")

# # --- Main client logic: connects, spawns handler threads ---
# def run_client(name):
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.connect((HOST, PORT))
#     send_msg(sock, {'type':'CONNECT','from':name})
#     resp = recv_msg(sock)
#     if resp and resp.get('type') == 'CONNECTED':
#         print(f"Connected as {name}")
#     else:
#         print("Failed to connect:", resp); return

#     sender = Sender(sock, name)
#     receiver = Receiver(sock, name, sender)

#     # start thread to read messages from server
#     def recv_loop():
#         while True:
#             m = recv_msg(sock)
#             if m is None:
#                 print("Server closed")
#                 os._exit(0)
#             mtype = m.get('type')
#             if mtype in ('MSG','FILE_CHUNK'):
#                 receiver.process_segment(m)
#             elif mtype == 'FILE_META':
#                 # meta indicates filename and key
#                 meta = m.get('meta')
#                 frm = m.get('from')
#                 key_b64 = meta.get('key')
#                 key = base64.b64decode(key_b64)
#                 fname = f"recv_from_{frm}_" + meta.get('fname')
#                 # assemble chunks previously stored
#                 rec = receiver.files.get(frm)
#                 if not rec:
#                     rec = {'chunks':[], 'meta':None}
#                 rec['meta'] = meta
#                 # combine encrypted bytes and decrypt
#                 enc = b''.join(rec['chunks'])
#                 dec = decrypt_bytes(enc, key)
#                 with open(fname, 'wb') as fout:
#                     fout.write(dec)
#                 print(f"[CLIENT] Wrote decrypted file to {fname} (size {len(dec)})")
#                 # cleanup
#                 receiver.files.pop(frm, None)
#             elif mtype == 'ACK':
#                 # server-echoed ACKs could arrive; but in our simple design receiver triggers sender.handle_ack
#                 pass
#             elif mtype == 'JOINED':
#                 print(f"Joined room {m.get('room')}")
#             elif mtype == 'LEFT':
#                 print(f"Left room {m.get('room')}")
#             elif mtype == 'ERROR':
#                 print("Error from server:", m.get('why'))
#             else:
#                 # other notifications
#                 print("Server message:", m)

#     threading.Thread(target=recv_loop, daemon=True).start()

#     # keep alive
#     while True:
#         time.sleep(1)

# if __name__ == '__main__':
#     parser = argparse.ArgumentParser()
#     parser.add_argument('--name', required=True)
#     args = parser.parse_args()
#     run_client(args.name)





"""

# import required modules
import socket
import threading
import tkinter as tk
from tkinter import scrolledtext
from tkinter import messagebox

HOST = '127.0.0.1'
PORT = 1234

DARK_GREY = '#121212'
MEDIUM_GREY = '#1F1B24'
OCEAN_BLUE = '#464EB8'
WHITE = "white"
FONT = ("Helvetica", 17)
BUTTON_FONT = ("Helvetica", 15)
SMALL_FONT = ("Helvetica", 13)

# Creating a socket object
# AF_INET: we are going to use IPv4 addresses
# SOCK_STREAM: we are using TCP packets for communication
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

def add_message(message):
    message_box.config(state=tk.NORMAL)
    message_box.insert(tk.END, message + '\n')
    message_box.config(state=tk.DISABLED)

def connect():

    # try except block
    try:

        # Connect to the server
        client.connect((HOST, PORT))
        print("Successfully connected to server")
        add_message("[SERVER] Successfully connected to the server")
    except:
        messagebox.showerror("Unable to connect to server", f"Unable to connect to server {HOST} {PORT}")

    username = username_textbox.get()
    if username != '':
        client.sendall(username.encode())
    else:
        messagebox.showerror("Invalid username", "Username cannot be empty")

    threading.Thread(target=listen_for_messages_from_server, args=(client, )).start()

    username_textbox.config(state=tk.DISABLED)
    username_button.config(state=tk.DISABLED)

def send_message():
    message = message_textbox.get()
    if message != '':
        client.sendall(message.encode())
        message_textbox.delete(0, len(message))
    else:
        messagebox.showerror("Empty message", "Message cannot be empty")

root = tk.Tk()
root.geometry("600x600")
root.title("Messenger Client")
root.resizable(False, False)

root.grid_rowconfigure(0, weight=1)
root.grid_rowconfigure(1, weight=4)
root.grid_rowconfigure(2, weight=1)

top_frame = tk.Frame(root, width=600, height=100, bg=DARK_GREY)
top_frame.grid(row=0, column=0, sticky=tk.NSEW)

middle_frame = tk.Frame(root, width=600, height=400, bg=MEDIUM_GREY)
middle_frame.grid(row=1, column=0, sticky=tk.NSEW)

bottom_frame = tk.Frame(root, width=600, height=100, bg=DARK_GREY)
bottom_frame.grid(row=2, column=0, sticky=tk.NSEW)

username_label = tk.Label(top_frame, text="Enter username:", font=FONT, bg=DARK_GREY, fg=WHITE)
username_label.pack(side=tk.LEFT, padx=10)

username_textbox = tk.Entry(top_frame, font=FONT, bg=MEDIUM_GREY, fg=WHITE, width=23)
username_textbox.pack(side=tk.LEFT)

username_button = tk.Button(top_frame, text="Join", font=BUTTON_FONT, bg=OCEAN_BLUE, fg=WHITE, command=connect)
username_button.pack(side=tk.LEFT, padx=15)

message_textbox = tk.Entry(bottom_frame, font=FONT, bg=MEDIUM_GREY, fg=WHITE, width=38)
message_textbox.pack(side=tk.LEFT, padx=10)

message_button = tk.Button(bottom_frame, text="Send", font=BUTTON_FONT, bg=OCEAN_BLUE, fg=WHITE, command=send_message)
message_button.pack(side=tk.LEFT, padx=10)

message_box = scrolledtext.ScrolledText(middle_frame, font=SMALL_FONT, bg=MEDIUM_GREY, fg=WHITE, width=67, height=26.5)
message_box.config(state=tk.DISABLED)
message_box.pack(side=tk.TOP)


def listen_for_messages_from_server(client):

    while 1:

        message = client.recv(2048).decode('utf-8')
        if message != '':
            username = message.split("~")[0]
            content = message.split('~')[1]

            add_message(f"[{username}] {content}")
            
        else:
            messagebox.showerror("Error", "Message recevied from client is empty")

# main function
def main():

    root.mainloop()
    
if __name__ == '__main__':
    main()

"""
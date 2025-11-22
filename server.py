# # server.py
# # Simple ChatChat server that routes messages and maintains rooms.
# # Use: python server.py
# import socket
# import threading
# import json
# import struct
# import time

# HOST = '127.0.0.1'
# PORT = 9009

# # Helper framing: 4-byte length header then JSON bytes
# def send_msg(conn, obj):
#     raw = json.dumps(obj, separators=(',',':')).encode('utf-8')
#     try:
#         conn.sendall(struct.pack('!I', len(raw)) + raw)
#         return True
#     except Exception as e:
#         # connection likely closed / broken pipe
#         print('[SERVER] send_msg failed:', e)
#         try:
#             conn.close()
#         except:
#             pass
#         return False

# def recv_msg(conn):
#     # read exact n bytes helper
#     def _recvall(n):
#         buf = b''
#         while len(buf) < n:
#             chunk = conn.recv(n - len(buf))
#             if not chunk:
#                 return None
#             buf += chunk
#         return buf

#     hdr = _recvall(4)
#     if not hdr:
#         return None
#     try:
#         (n,) = struct.unpack('!I', hdr)
#     except Exception:
#         return None
#     data = _recvall(n)
#     if data is None:
#         return None
#     try:
#         return json.loads(data.decode('utf-8'))
#     except json.JSONDecodeError:
#         # malformed JSON from client
#         print('[SERVER] malformed JSON from client')
#         return None

# class ClientState:
#     def __init__(self, conn, addr, name):
#         self.conn = conn
#         self.addr = addr
#         self.name = name
#         self.room = None
#         self.lock = threading.Lock()

# class ChatServer:
#     def __init__(self, host, port):
#         self.addr = (host, port)
#         self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         self.clients = {}  # name -> ClientState
#         self.rooms = {}    # room -> set(names)
#         self.lock = threading.Lock()

#     def start(self):
#         self.sock.bind(self.addr)
#         self.sock.listen(50)
#         print(f"[SERVER] Listening on {self.addr}")
#         while True:
#             conn, addr = self.sock.accept()
#             threading.Thread(target=self._handle_new, args=(conn, addr), daemon=True).start()

#     def _handle_new(self, conn, addr):
#         try:
#             # first message must be a CONNECT with a name
#             msg = recv_msg(conn)
#             if not msg or msg.get('type') != 'CONNECT':
#                 conn.close(); return
#             name = msg.get('from')
#             if not name:
#                 send_msg(conn, {'type':'ERROR','why':'no name'}); conn.close(); return
#             with self.lock:
#                 if name in self.clients:
#                     send_msg(conn, {'type':'ERROR','why':'name taken'}); conn.close(); return
#                 cs = ClientState(conn, addr, name)
#                 self.clients[name] = cs
#             print(f"[SERVER] {name} connected from {addr}")
#             send_msg(conn, {'type':'CONNECTED','you':name})
#             # start per-client listener
#             while True:
#                 m = recv_msg(conn)
#                 if m is None:
#                     break
#                 self._route(cs, m)
#         except Exception as e:
#             print("[SERVER] client handler exception:", e)
#         finally:
#             self._disconnect(name)

#     def _disconnect(self, name):
#         with self.lock:
#             cs = self.clients.pop(name, None)
#             if not cs: return
#             if cs.room and cs.room in self.rooms:
#                 self.rooms[cs.room].discard(name)
#             try:
#                 cs.conn.close()
#             except:
#                 pass
#             print(f"[SERVER] {name} disconnected")

#     def _route(self, cs: ClientState, msg):
#         mtype = msg.get('type')
#         if mtype == 'JOIN':
#             room = msg.get('room')
#             if not room: return
#             with self.lock:
#                 self.rooms.setdefault(room, set()).add(cs.name)
#                 cs.room = room
#             print(f"[SERVER] {cs.name} joined room {room}")
#             send_msg(cs.conn, {'type':'JOINED','room':room})
#         elif mtype == 'LEAVE':
#             room = cs.room
#             with self.lock:
#                 if room and room in self.rooms:
#                     self.rooms[room].discard(cs.name)
#                     cs.room = None
#             send_msg(cs.conn, {'type':'LEFT','room':room})
#         elif mtype == 'MSG':
#             dest = msg.get('to')
#             if dest:  # direct message
#                 with self.lock:
#                     dest_cs = self.clients.get(dest)
#                 if dest_cs:
#                     send_msg(dest_cs.conn, msg)
#                 else:
#                     send_msg(cs.conn, {'type':'ERROR','why':'no such user'})
#             else:  # room
#                 room = msg.get('room') or cs.room
#                 if not room:
#                     send_msg(cs.conn, {'type':'ERROR','why':'not in room'})
#                     return
#                 with self.lock:
#                     members = list(self.rooms.get(room, []))
#                 for name in members:
#                     if name == cs.name: continue
#                     target = self.clients.get(name)
#                     if target:
#                         send_msg(target.conn, msg)
#         elif mtype in ('FILE_META','FILE_CHUNK'):
#             # file routing uses same logic as MSG (either to or room)
#             dest = msg.get('to')
#             if dest:
#                 with self.lock:
#                     target = self.clients.get(dest)
#                 if target:
#                     send_msg(target.conn, msg)
#             else:
#                 room = msg.get('room') or cs.room
#                 if not room:
#                     send_msg(cs.conn, {'type':'ERROR','why':'not in room'})
#                     return
#                 with self.lock:
#                     members = list(self.rooms.get(room, []))
#                 for name in members:
#                     if name == cs.name: continue
#                     t = self.clients.get(name)
#                     if t:
#                         send_msg(t.conn, msg)
#         else:
#             # unknown: echo back
#             send_msg(cs.conn, {'type':'ERROR','why':'unknown type'})

# if __name__ == '__main__':
#     s = ChatServer(HOST, PORT)
#     s.start()







"""
# Import required modules
import socket
import threading

HOST = '127.0.0.1'
PORT = 1234 # You can use any port between 0 to 65535
LISTENER_LIMIT = 5
active_clients = [] # List of all currently connected users

# Function to listen for upcoming messages from a client
def listen_for_messages(client, username):

    while 1:

        message = client.recv(2048).decode('utf-8')
        if message != '':
            
            final_msg = username + '~' + message
            send_messages_to_all(final_msg)

        else:
            print(f"The message send from client {username} is empty")


# Function to send message to a single client
def send_message_to_client(client, message):

    client.sendall(message.encode())

# Function to send any new message to all the clients that
# are currently connected to this server
def send_messages_to_all(message):
    
    for user in active_clients:

        send_message_to_client(user[1], message)

# Function to handle client
def client_handler(client):
    
    # Server will listen for client message that will
    # Contain the username
    while 1:

        username = client.recv(2048).decode('utf-8')
        if username != '':
            active_clients.append((username, client))
            prompt_message = "SERVER~" + f"{username} added to the chat"
            send_messages_to_all(prompt_message)
            break
        else:
            print("Client username is empty")

    threading.Thread(target=listen_for_messages, args=(client, username, )).start()

# Main function
def main():

    # Creating the socket class object
    # AF_INET: we are going to use IPv4 addresses
    # SOCK_STREAM: we are using TCP packets for communication
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Creating a try catch block
    try:
        # Provide the server with an address in the form of
        # host IP and port
        server.bind((HOST, PORT))
        print(f"Running the server on {HOST} {PORT}")
    except:
        print(f"Unable to bind to host {HOST} and port {PORT}")

    # Set server limit
    server.listen(LISTENER_LIMIT)

    # This while loop will keep listening to client connections
    while 1:

        client, address = server.accept()
        print(f"Successfully connected to client {address[0]} {address[1]}")

        threading.Thread(target=client_handler, args=(client, )).start()


if __name__ == '__main__':
    main()

"""
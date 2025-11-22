// wsClient.js
// Simple native WebSocket wrapper with automatic JSON parsing/stringify
export default class WSClient {
  constructor(baseUrl, userId) {
    this.userId = userId;
    this.url = `${baseUrl.replace(/\/$/, "")}/ws/${userId}`;
    this.socket = null;
    this.handlers = {}; // map type -> fn
  }

  connect() {
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => {
      console.log("ws open");
    };
    this.socket.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const type = data.type;
        const h = this.handlers[type];
        if (h) h(data);
        else console.log("ws msg", data);
      } catch (e) {
        console.error("ws parse err", e);
      }
    };
    this.socket.onclose = () => console.log("ws closed");
  }

  on(type, handler) {
    this.handlers[type] = handler;
  }

  send(obj) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      console.error("socket not open");
      return;
    }
    this.socket.send(JSON.stringify(obj));
  }

  join(room) {
    this.send({ type: "join", room });
  }
  leave(room) {
    this.send({ type: "leave", room });
  }
  sendMessage(room, text, require_ack = false) {
    this.send({ type: "send", room, text, require_ack });
  }
  ack(msg_id) {
    this.send({ type: "ack", msg_id });
  }
}

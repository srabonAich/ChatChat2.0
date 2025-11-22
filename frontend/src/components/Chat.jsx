import React, { useEffect, useState } from "react";
import WSClient from "../wsClient";
import axios from "axios";

export default function Chat({ userId, roomId }) {
  const [wsClient, setWsClient] = useState(null);
  const [messages, setMessages] = useState([]);

  useEffect(() => {
    const client = new WSClient("http://localhost:8000", userId);
    client.connect();
    client.on("system", (d) => console.log("system", d));
    client.on("message", (m) => {
      // server sends message objects: {type:"message", room, text?, file_id?, from, msg_id?}
      setMessages((prev) => [...prev, m]);
      // if message has msg_id and requires ack, send ack back
      if (m.msg_id) {
        client.ack(m.msg_id);
      }
    });
    setWsClient(client);
    // join room after small delay to ensure open
    setTimeout(() => client.join(roomId), 200);

    return () => {
      if (client) client.leave(roomId);
      // closing socket omitted for brevity
    };
  }, [userId, roomId]);

  const sendText = (text, requireAck = false) => {
    wsClient.sendMessage(roomId, text, requireAck);
  };

  const uploadFile = async (file) => {
    const form = new FormData();
    form.append("file", file);
    form.append("room_id", roomId);
    form.append("sender_id", userId);
    await axios.post("http://localhost:8000/upload/", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  };

  return (
    <div>
      <div>
        {messages.map((m, i) => (
          <div key={i}>
            <strong>{m.from}</strong>:{" "}
            {m.text ||
              (m.filename ? (
                <a href={`http://localhost:8000/file/${m.file_id}`}>
                  {m.filename}
                </a>
              ) : null)}
            {m.msg_id ? <span> (msg_id: {m.msg_id})</span> : null}
          </div>
        ))}
      </div>
      <button onClick={() => sendText("hello", true)}>
        Send (require ACK)
      </button>
      <input type="file" onChange={(e) => uploadFile(e.target.files[0])} />
    </div>
  );
}

import React, {useEffect, useState, useRef} from 'react';

function App(){
  const [connected, setConnected] = useState(false);
  const [name, setName] = useState('Alice');
  const [messages, setMessages] = useState([]);
  const wsRef = useRef(null);

  useEffect(()=>{
    return ()=>{
      if(wsRef.current) wsRef.current.close();
    }
  },[])

  const connect = ()=>{
    const ws = new WebSocket(`ws://localhost:9009/ws?name=${encodeURIComponent(name)}`);
    ws.onopen = ()=>{
      setConnected(true);
      setMessages(m => [...m, {from:'system', text:'connected'}]);
    }
    ws.onmessage = (ev)=>{
      try{
        const msg = JSON.parse(ev.data);
        setMessages(m => [...m, {from: msg.from || 'server', text: JSON.stringify(msg)}]);
      }catch(e){
        setMessages(m => [...m, {from:'server', text:ev.data}]);
      }
    }
    ws.onclose = ()=>{ setConnected(false); setMessages(m => [...m, {from:'system', text:'disconnected'}]); }
    wsRef.current = ws;
  }

  const sendMsg = (text)=>{
    if(!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const msg = {type:'MSG', from:name, room:null, payload:btoa(text)}; // frontend uses base64 payload
    wsRef.current.send(JSON.stringify(msg));
  }

  return (
    <div style={{padding:20}}>
      <h2>ChatChat (demo)</h2>
      <div>
        <label>Name: <input value={name} onChange={e=>setName(e.target.value)} /></label>
        <button onClick={connect} disabled={connected}>Connect</button>
      </div>
      <div style={{marginTop:10}}>
        <MessageList messages={messages} />
        <SendBox onSend={(t)=>sendMsg(t)} disabled={!connected} />
      </div>
    </div>
  )
}

function MessageList({messages}){
  return (
    <div style={{border:'1px solid #ccc', height:300, overflow:'auto', padding:8}}>
      {messages.map((m,i)=>(<div key={i}><b>{m.from}:</b> {m.text}</div>))}
    </div>
  )
}

function SendBox({onSend, disabled}){
  const [v, setV] = useState('Hello');
  return (
    <div style={{marginTop:8}}>
      <input value={v} onChange={e=>setV(e.target.value)} style={{width:'60%'}} />
      <button onClick={()=>onSend(v)} disabled={disabled}>Send</button>
    </div>
  )
}

export default App;

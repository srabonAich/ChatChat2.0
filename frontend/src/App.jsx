import React, {useEffect, useState, useRef} from 'react';
import './App.css';

function App(){
  const [connected, setConnected] = useState(false);
  const [name, setName] = useState('Alice');
  const [messages, setMessages] = useState([]);
  const [clientsList, setClientsList] = useState([]);
  const [selectedRecipient, setSelectedRecipient] = useState('');
  const [transfersProgress, setTransfersProgress] = useState({}); // transfer_id -> {acked, total}
  const wsRef = useRef(null);
  const transfersRef = useRef({}); // transfer_id -> {meta, chunks: Map(index->Uint8Array), received, next_expected}
  const sentTransfersRef = useRef({}); // outgoing transfer_id -> {meta, total_chunks, acked_up_to, payloads: Map(idx->b64), sentAt: Map(idx->ts)}

  // Congestion control defaults (measured in chunks)
  const INITIAL_CWND = 1; // start with 1 chunk
  const INITIAL_SSTHRESH = 64; // arbitrary large
  const ALPHA = 0.125; // for RTT smoothing
  const BETA = 0.25;

  useEffect(()=>{
    return ()=>{
      if(wsRef.current) wsRef.current.close();
    }
  },[])

  const connect = ()=>{
    const ws = new WebSocket(`ws://localhost:9009/ws?name=${encodeURIComponent(name)}`);
    ws.onopen = async ()=>{
      console.log('[WS] open', {name});
      setConnected(true);
      setMessages(m => [...m, {from:'system', text:'connected'}]);
      // fetch connected clients for recipient selection
      try{
        const res = await fetch('http://localhost:9009/clients');
        const j = await res.json();
        const list = j.clients || [];
        setClientsList(list);
        const others = list.filter(c=>c !== name);
        if(others.length>0) setSelectedRecipient(others[0]);
      }catch(e){ console.warn('could not fetch clients', e); }
    }
    ws.onmessage = (ev)=>{
      // log raw incoming frame for debugging
      try{ console.log('[WS] in', ev.data); }catch(e){}
      try{
        const msg = JSON.parse(ev.data);
        // special internal server ack when server persisted a chunk
        if(msg.type === 'SERVER_RECV_CHUNK'){
          console.log('[SERVER] recv chunk ack', msg);
          setMessages(m => [...m, {from:'server', text:`Server persisted chunk ${msg.chunk_index} for ${msg.transfer_id}`}]);
          return;
        }
        // handle ACKs and file transfer messages specially
  if(msg.type === 'ACK'){
          // update outgoing transfer progress if we sent this transfer
          const tid = msg.transfer_id;
          if(tid && sentTransfersRef.current[tid]){
            const entry = sentTransfersRef.current[tid];
            const prevAck = entry.acked_up_to || 0;
            const newAck = Math.max(prevAck, msg.ack || 0);
            // ensure cc state
            entry.cc = entry.cc || {cwnd: INITIAL_CWND, ssthresh: INITIAL_SSTHRESH, inFlight: new Set(), nextToSend: 0, lastAck: 0, dupAcks: 0, srtt:500, rto:1000, rttvar:500};

            if(newAck > prevAck){
              // advanced ack
              // remove acked chunks from inFlight and payloads
              for(let i=prevAck;i<newAck;i++){
                if(entry.cc && entry.cc.inFlight) entry.cc.inFlight.delete(i);
                if(entry.payloads) { delete entry.payloads[i]; }
                if(entry.sentAt) { delete entry.sentAt[i]; }
              }

              // RTT estimation using the last newly ACKed chunk
              try{
                const sampleIdx = newAck - 1;
                const sentTs = entry.sentAt && entry.sentAt[sampleIdx];
                if(sentTs){
                  const sampleRTT = Date.now() - sentTs;
                  // initialize rttvar if missing
                  entry.cc.rttvar = entry.cc.rttvar || (entry.cc.srtt/2 || 250);
                  // SRTT update
                  entry.cc.srtt = (1 - ALPHA) * entry.cc.srtt + ALPHA * sampleRTT;
                  entry.cc.rttvar = (1 - BETA) * entry.cc.rttvar + BETA * Math.abs(entry.cc.srtt - sampleRTT);
                  entry.cc.rto = Math.max(200, Math.floor(entry.cc.srtt + 4 * entry.cc.rttvar));
                }
              }catch(e){ console.warn('rtt update failed', e); }

              // congestion control: slow start or congestion avoidance
              if(entry.cc.cwnd < entry.cc.ssthresh){
                // increase by number of newly acked segments (slow start)
                entry.cc.cwnd += (newAck - prevAck);
              }else{
                // congestion avoidance: additive increase ~ 1 full segment per RTT
                entry.cc.cwnd += (newAck - prevAck) * (1.0 / Math.max(1, entry.cc.cwnd));
              }

              entry.cc.dupAcks = 0;
              entry.cc.lastAck = newAck;
              entry.acked_up_to = newAck;
              console.log('[ACK] advanced', {transfer_id: tid, newAck, cwnd: entry.cc.cwnd, rto: entry.cc.rto});
              setMessages(m => [...m, {from:'system', text:`ACK for ${tid}: cumulative ack=${entry.acked_up_to}/${entry.total_chunks} cwnd=${entry.cc.cwnd.toFixed(2)} rto=${entry.cc.rto}`}]);
              setTransfersProgress(p=>({...p, [tid]: {acked: entry.acked_up_to, total: entry.total_chunks}}));
            }else{
              // duplicate ACK
              entry.cc.dupAcks = (entry.cc.dupAcks || 0) + 1;
              setMessages(m => [...m, {from:'system', text:`Dup-ACK (${entry.cc.dupAcks}) for ${tid} ack=${newAck}`}]);
              if(entry.cc.dupAcks >= 3){
                // fast retransmit
                entry.cc.ssthresh = Math.max(1, Math.floor(entry.cc.cwnd / 2));
                entry.cc.cwnd = entry.cc.ssthresh + 3;
                const toResend = newAck;
                const p = entry.payloads && entry.payloads[toResend];
                if(p){
                    try{
                    const out = {type:'FILE_CHUNK', from:name, to: entry.to || null, room: entry.room || null, transfer_id: tid, chunk_index: toResend, total_chunks: entry.total_chunks, payload: p};
                    console.log('[fast-retransmit] out', out);
                    wsRef.current.send(JSON.stringify(out));
                    entry.sentAt[toResend] = Date.now();
                    setMessages(m=>[...m,{from:'system', text:`[fast-retransmit] resent ${toResend} for ${tid}`}]);
                  }catch(e){console.warn('fast retransmit failed', e)}
                }
              }
            }
            // cleanup stored payloads below ack (defensive)
            for(let k in (entry.payloads||{})){
              const idx = parseInt(k,10);
              if(idx < entry.acked_up_to){ delete entry.payloads[k]; delete entry.sentAt[k]; }
            }
          }
          return;
        }
        if(msg.type === 'CLIENTS'){
          const list = msg.clients || [];
          setClientsList(list);
          const others = list.filter(c=>c !== name);
          // ensure selectedRecipient stays valid; use functional update to avoid stale closure
          setSelectedRecipient(prev => (prev && list.includes(prev)) ? prev : (others.length>0 ? others[0] : ''));
          return;
        }
        // handle text messages
        if(msg.type === 'MSG'){
          try{
            const txt = msg.payload ? atob(msg.payload) : msg.text || '';
            setMessages(m => [...m, {from: msg.from || 'server', text: txt}]);
          }catch(e){
            setMessages(m => [...m, {from: msg.from || 'server', text: JSON.stringify(msg)}]);
          }
          return;
        }

        // handle file transfer messages
        if(msg.type === 'FILE_META'){
          const meta = msg.meta || {};
          const tid = meta.transfer_id || msg.transfer_id;
          if(tid){
            transfersRef.current[tid] = {meta, chunks: new Map(), received: 0, total: meta.total_chunks || null, from: msg.from, next_expected: 0};
            console.log('[FILE_META] in', {meta, from: msg.from});
            setMessages(m => [...m, {from: 'server', text: `Incoming file meta ${meta.fname} (${meta.size} bytes) id=${tid}`}]);
          } else {
            setMessages(m => [...m, {from: 'server', text: JSON.stringify(msg)}]);
          }
          return;
        }
        if(msg.type === 'FILE_READY'){
          const tid = msg.transfer_id;
          const fname = msg.fname;
          const url = `http://localhost:9009/file/${tid}`;
          setMessages(m => [...m, {from:'system', text:`File ready: ${fname} (download)`, link: url, fname}]);
          // ensure progress shows complete
          setTransfersProgress(p=>({...p, [tid]: {acked: (p[tid] && p[tid].total) || null, total: (p[tid] && p[tid].total) || null}}));
          return;
        }
        if(msg.type === 'FILE_CHUNK'){
          const tid = msg.transfer_id || (msg.meta||{}).transfer_id;
          const idx = msg.chunk_index;
          if(tid != null && idx != null){
            let t = transfersRef.current[tid];
            if(!t){
              // no meta yet; create placeholder
              t = transfersRef.current[tid] = {meta: null, chunks: new Map(), received:0, total: null, from: msg.from};
            }
            // decode base64 payload to binary (Uint8Array)
            const raw = atob(msg.payload);
            const buf = new Uint8Array(raw.length);
            for(let i=0;i<raw.length;i++) buf[i]=raw.charCodeAt(i);
            t.chunks.set(idx, buf);
            t.received += 1;
            if(t.total == null && msg.total_chunks) t.total = msg.total_chunks;
            console.log('[FILE_CHUNK] in', {transfer_id: tid, chunk_index: idx, from: msg.from});
            setMessages(m => [...m, {from: 'server', text: `Received chunk ${idx} for ${tid}`}]);
            // advance next_expected as long as contiguous chunks exist
            while(t.chunks.has(t.next_expected)){
              t.next_expected += 1;
            }
            // send cumulative ACK (next expected index) for this transfer back to sender via server
            try{
              const ackMsg = {type:'ACK', from:name, to: msg.from, transfer_id: tid, ack: t.next_expected};
              console.log('[ACK] out', ackMsg);
              wsRef.current.send(JSON.stringify(ackMsg));
              setMessages(m => [...m, {from:'system', text:`Sent cumulative ACK ${t.next_expected} for ${tid}`}]);
            }catch(e){
              console.warn('ACK send failed', e);
            }

            // check completion
            if(t.total != null && t.chunks.size >= t.total){
              // assemble
              const parts = [];
              for(let i=0;i<t.total;i++){
                const part = t.chunks.get(i);
                if(!part){
                  setMessages(m => [...m, {from:'system', text:`Missing chunk ${i} for ${tid}`}]);
                  return;
                }
                parts.push(part);
              }
              // concat into blob and offer download
              const blob = new Blob(parts);
              const url = URL.createObjectURL(blob);
              const fname = (t.meta && t.meta.fname) ? t.meta.fname : `file_${tid}`;
              setMessages(m => [...m, {from:'system', text: `File received: ${fname}`, link: url, fname}]);
              // cleanup
              delete transfersRef.current[tid];
            }
          } else {
            setMessages(m => [...m, {from: msg.from || 'server', text: JSON.stringify(msg)}]);
          }
          return;
        }
        // default: push message (stringified)
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
    const msg = {type:'MSG', from:name, payload:btoa(text)}; // frontend uses base64 payload
    if(selectedRecipient && selectedRecipient !== name) msg.to = selectedRecipient;
    console.log('[WS] out MSG', msg);
    wsRef.current.send(JSON.stringify(msg));
  }

  // --- file send ---
  const CHUNK_SIZE = 64 * 1024; // 64KB
  const RETRANSMIT_MS = 3000;

  // retransmit monitor
  useEffect(()=>{
    const iv = setInterval(()=>{
      if(!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
      const now = Date.now();
      for(const tid in sentTransfersRef.current){
        const entry = sentTransfersRef.current[tid];
        const ack = entry.acked_up_to || 0;
        const total = entry.total_chunks || 0;
        for(let i=ack;i<total;i++){
          const last = entry.sentAt && entry.sentAt[i];
          if(!entry.payloads || !entry.payloads[i]) continue; // nothing to resend
          if(!last || (now - last) > RETRANSMIT_MS){
            // resend chunk
            const chunkMsg = {type:'FILE_CHUNK', from:name, to: entry.to || null, room: entry.room || null, transfer_id: tid, chunk_index:i, total_chunks: total, payload: entry.payloads[i]};
            try{ wsRef.current.send(JSON.stringify(chunkMsg)); entry.sentAt[i] = Date.now(); setMessages(m=>[...m,{from:'system', text:`Retransmitted chunk ${i} for ${tid}`}]); }catch(e){console.warn('retransmit failed', e)}
          }
        }
      }
    }, 1000);
    return ()=>clearInterval(iv);
  }, [name]);

  // Sender loop: responsible for sending while respecting cwnd and retransmit on RTO
    useEffect(()=>{
      const iv = setInterval(()=>{
        if(!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        const now = Date.now();
        for(const tid in sentTransfersRef.current){
          const entry = sentTransfersRef.current[tid];
          if(!entry) continue;
          // initialize cc state
          entry.cc = entry.cc || {cwnd: INITIAL_CWND, ssthresh: INITIAL_SSTHRESH, inFlight: new Set(), nextToSend: 0, lastAck: 0, dupAcks: 0, srtt: 500, rto: 1000};
          const cwndVal = Math.max(1, Math.floor(entry.cc.cwnd));
          // send while we have window and data
          while(entry.cc.inFlight.size < cwndVal && entry.nextToSend < entry.total_chunks){
            const i = entry.nextToSend;
            const payload = entry.payloads && entry.payloads[i];
            if(!payload) break; // nothing prepared yet
            const chunkMsg = {type:'FILE_CHUNK', from:name, transfer_id: tid, chunk_index:i, total_chunks: entry.total_chunks, payload};
            if(entry.to) chunkMsg.to = entry.to;
            if(entry.room) chunkMsg.room = entry.room;
            try{
              console.log('[WS] out FILE_CHUNK', {transfer_id: tid, chunk_index: i, to: chunkMsg.to});
              wsRef.current.send(JSON.stringify(chunkMsg));
              entry.sentAt[i] = Date.now();
              entry.cc.inFlight.add(i);
              entry.nextToSend += 1;
              setMessages(m=>[...m,{from:'system', text:`[send-loop] sent chunk ${i} for ${tid} (inflight=${entry.cc.inFlight.size}/${cwndVal})`}]);
            }catch(e){ console.warn('send in loop failed', e); break; }
          }

          // check timeouts for in-flight segments
          for(const idx of Array.from(entry.cc.inFlight)){
            const last = entry.sentAt && entry.sentAt[idx];
            if(!last) continue;
            if(now - last > (entry.cc.rto || 1000)){
              // timeout -> multiplicative decrease
              entry.cc.ssthresh = Math.max(1, Math.floor(entry.cc.cwnd/2));
              entry.cc.cwnd = 1;
              // retransmit first unacked (which is entry.acked_up_to)
              const toResend = entry.acked_up_to || 0;
              const p = entry.payloads && entry.payloads[toResend];
              if(p){
                const chunkMsg = {type:'FILE_CHUNK', from:name, transfer_id: tid, chunk_index: toResend, total_chunks: entry.total_chunks, payload: p};
                if(entry.to) chunkMsg.to = entry.to;
                if(entry.room) chunkMsg.room = entry.room;
                try{ wsRef.current.send(JSON.stringify(chunkMsg)); entry.sentAt[toResend]=Date.now(); setMessages(m=>[...m,{from:'system', text:`[timeout] retransmitted ${toResend} for ${tid}`}]); }catch(e){console.warn('retransmit failed', e)}
              }
              // reset inFlight to only unacked ones
              entry.cc.inFlight = new Set(Array.from(entry.cc.inFlight).filter(x=> x >= (entry.acked_up_to||0)));
              break; // back off to next interval
            }
          }
        }
      }, 200);
      return ()=>clearInterval(iv);
    }, [name]);

  const sendFile = async (file, to=null, room=null) =>{
    if(!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    // require an explicit recipient or room for one-to-one/room sends
    if(!to && !room){
      setMessages(m => [...m, {from:'system', text:'Select a recipient or room before sending a file'}]);
      return;
    }
    const transfer_id = (crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
    const total_chunks = Math.ceil(file.size / CHUNK_SIZE);
  // register outgoing transfer for ACK tracking + initialize cc and send pointers
  sentTransfersRef.current[transfer_id] = {meta:{fname:file.name,size:file.size}, total_chunks, acked_up_to:0, payloads:{}, sentAt:{}, to, room, nextToSend:0, cc: {cwnd: INITIAL_CWND, ssthresh: INITIAL_SSTHRESH, inFlight: new Set(), nextToSend:0, lastAck:0, dupAcks:0, srtt:500, rto:1000, rttvar:250}};
    const meta = {transfer_id, fname: file.name, size: file.size, total_chunks};
    // only include to/room keys when present to avoid server interpreting null as absent
    const metaMsg = {type:'FILE_META', from: name, meta};
    if(to) metaMsg.to = to;
    if(room) metaMsg.room = room;
  console.log('[WS] out FILE_META', metaMsg);
  wsRef.current.send(JSON.stringify(metaMsg));
    setMessages(m => [...m, {from:'you', text:`Sending file ${file.name} (${file.size} bytes) id=${transfer_id}`}]);

    for(let i=0;i<total_chunks;i++){
      const start = i * CHUNK_SIZE;
      const end = Math.min((i+1)*CHUNK_SIZE, file.size);
      const slice = file.slice(start, end);
      const arr = await slice.arrayBuffer();
      // convert to base64
      let binary = '';
      const u8 = new Uint8Array(arr);
      for(let j=0;j<u8.length;j++) binary += String.fromCharCode(u8[j]);
      const b64 = btoa(binary);
  // store payload for possible retransmit; actual sending is handled by sender loop (cwnd-aware)
  const entry = sentTransfersRef.current[transfer_id];
  entry.payloads[i] = b64;
  // mark not-yet-sent; sender loop will set sentAt[] when it sends
  setMessages(m => [...m, {from:'you', text:`Queued chunk ${i+1}/${total_chunks}`}]);
      // small throttle so UI remains responsive
      await new Promise(r=>setTimeout(r, 10));
    }
    setMessages(m => [...m, {from:'you', text:`File send queued: ${file.name}`}]);
  }

  return (
    <div style={{padding:20}}>
      <h2>ChatChat (demo)</h2>
      <div>
        <label>Name: <input value={name} onChange={e=>setName(e.target.value)} /></label>
        <button onClick={connect} disabled={connected}>Connect</button>
        <label style={{marginLeft:10}}>Recipient: <select value={selectedRecipient} onChange={e=>setSelectedRecipient(e.target.value)}>
          <option value="">(none)</option>
          {clientsList.filter(c=>c!==name).map(c=>(<option key={c} value={c}>{c}</option>))}
        </select></label>
        <label style={{marginLeft:10}}>File: <input id="file-input" type="file" disabled={!connected} /></label>
        <button style={{marginLeft:8}} onClick={()=>{
          const el = document.getElementById('file-input');
          if(el && el.files && el.files[0]) sendFile(el.files[0], selectedRecipient || null);
        }} disabled={!connected}>Send File</button>
      </div>
      <div style={{marginTop:10}}>
        <MessageList messages={messages} />
        <div style={{marginTop:8}}>
          <h4>Outgoing transfers</h4>
          {Object.keys(transfersProgress).length === 0 && <div style={{color:'#999'}}>No active transfers</div>}
          {Object.entries(transfersProgress).map(([tid, st])=>{
            const pct = st.total ? Math.round((st.acked||0)/st.total*100) : 0;
            return (<div key={tid} style={{marginBottom:6}}>
              <div style={{fontSize:12}}>{tid} — {st.acked||0}/{st.total||'?'} ({pct}%)</div>
              <div style={{background:'#333', height:8, borderRadius:4}}><div style={{width:`${pct}%`, height:'100%', background:'#46d'}} /></div>
            </div>)
          })}
        </div>
        <SendBox onSend={(t)=>sendMsg(t)} disabled={!connected} />
      </div>
    </div>
  )
}

function MessageList({messages}){
  return (
    <div style={{border:'1px solid #ccc', height:300, overflow:'auto', padding:8}}>
      {messages.map((m,i)=>(
        <div key={i} style={{marginBottom:6}}>
          <b>{m.from}:</b>
          {' '}
          {m.link ? (<a href={m.link} download={m.fname || ''}>{m.text || m.fname || 'download'}</a>) : (<span>{m.text}</span>)}
        </div>
      ))}
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

export default App

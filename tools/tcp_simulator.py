# tcp_simulator.py
import time
import random
from collections import deque

class TCPSim:
    def __init__(self, loss_prob=0.15):
        self.cwnd = 1
        self.ssthresh = 16
        self.mss = 1
        self.unacked = deque()   # packets sent but not acked
        self.next_seq = 1
        self.rto = 2.0
        self.loss_prob = loss_prob

    def send(self):
        allowed = int(self.cwnd - len(self.unacked))
        for _ in range(max(0, allowed)):
            pkt = {"seq": self.next_seq, "sent": time.time(), "retries": 0}
            self.unacked.append(pkt)
            print(f"Sent pkt {pkt['seq']} (cwnd={self.cwnd:.1f})")
            self.next_seq += 1

    def receive_acks(self):
        # simulate ack for first unacked packet with probability (1-loss)
        if not self.unacked:
            return
        first = self.unacked[0]
        if random.random() > self.loss_prob:
            acked = self.unacked.popleft()
            print(f"ACK {acked['seq']}")
            # update cwnd: slow start or additive increase
            if self.cwnd < self.ssthresh:
                self.cwnd += 1  # linear for demo (or multiply)
            else:
                self.cwnd += 1/self.cwnd  # small additive
            if self.cwnd > 64:
                self.cwnd = 64
        else:
            print(f"Packet {first['seq']} lost (no ACK)")

    def timeout_check(self):
        if self.unacked:
            pkt = self.unacked[0]
            if time.time() - pkt["sent"] > self.rto:
                # timeout -> retransmit and reduce cwnd
                print(f"Timeout on pkt {pkt['seq']}, retransmit, reduce cwnd")
                self.ssthresh = max(2, int(self.cwnd/2))
                self.cwnd = 1
                pkt["sent"] = time.time()
                pkt["retries"] += 1

    def step(self):
        self.send()
        self.receive_acks()
        self.timeout_check()
        print(f"STATE: cwnd={self.cwnd:.2f}, ssthresh={self.ssthresh}, unacked={len(self.unacked)}")
        print("-"*40)
        time.sleep(1)

if __name__ == "__main__":
    sim = TCPSim(loss_prob=0.2)
    for _ in range(60):
        sim.step()

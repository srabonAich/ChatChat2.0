"""
Simple application-level flow and congestion control primitives (educational).
This module provides small classes to track cwnd/ssthresh and a basic retransmit scheduler.
These are intentionally minimal and documented so you can expand them.
"""
import time
from dataclasses import dataclass

@dataclass
class CongestionControl:
    cwnd: int
    ssthresh: int
    mss: int

    def on_ack(self, acked_bytes:int):
        # simple TCP-like slow start + congestion avoidance
        if self.cwnd < self.ssthresh:
            # slow start
            self.cwnd += self.mss
        else:
            # additive increase (very small step)
            self.cwnd += max(1, int(self.mss * (self.mss / max(1, self.cwnd))))

    def on_timeout(self):
        # multiplicative decrease
        self.ssthresh = max(self.cwnd // 2, self.mss)
        self.cwnd = self.mss


class FlowControl:
    def __init__(self, recv_window_bytes:int):
        self.rwnd = recv_window_bytes

    def advertise(self):
        return self.rwnd


# A light-weight retransmit scheduler (blocking sleep-based) is provided for demos.
class RetransmitTimer:
    def __init__(self, timeout=1.0):
        self.timeout = timeout
        self.start_time = None

    def start(self):
        self.start_time = time.time()

    def expired(self):
        if self.start_time is None: return False
        return (time.time() - self.start_time) >= self.timeout

    def reset(self):
        self.start_time = None

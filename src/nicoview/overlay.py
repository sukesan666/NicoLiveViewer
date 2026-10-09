from collections import deque
import threading

class OverlayQueue:
    def __init__(self): self.pending=deque(maxlen=500); self.lock=threading.Lock()
    def add(self, comment):
        with self.lock: self.pending.append(comment)
    def clear(self):
        with self.lock: self.pending.clear()
    def ready(self, now, delay=0):
        result=[]
        with self.lock:
            while self.pending and self.pending[0].received_at+delay <= now:
                c=self.pending.popleft()
                if now-c.received_at-delay < 10: result.append(c)
        return result

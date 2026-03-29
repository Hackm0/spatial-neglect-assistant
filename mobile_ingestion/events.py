from __future__ import annotations

from collections import defaultdict, deque
from queue import Empty, Queue
from threading import Lock
from time import time
from typing import Any


class SessionEventBroadcaster:
  """In-memory per-session event fanout with bounded history."""

  def __init__(self, *, history_size: int = 50) -> None:
    self._history_size = max(1, int(history_size))
    self._lock = Lock()
    self._subscribers: dict[str, set[Queue[dict[str, Any] | None]]] = defaultdict(set)
    self._history: dict[str, deque[dict[str, Any]]] = defaultdict(
        lambda: deque(maxlen=self._history_size))

  def broadcast(self, session_id: str, event_type: str,
                payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "type": event_type,
        "payload": payload or {},
        "ts": time(),
    }
    with self._lock:
      self._history[session_id].append(event)
      subscribers = tuple(self._subscribers.get(session_id, set()))

    for queue in subscribers:
      try:
        queue.put_nowait(event)
      except Exception:
        continue
    return event

  def subscribe(self, session_id: str) -> Queue[dict[str, Any] | None]:
    queue: Queue[dict[str, Any] | None] = Queue()
    with self._lock:
      self._subscribers[session_id].add(queue)
    return queue

  def unsubscribe(self, session_id: str,
                  queue: Queue[dict[str, Any] | None]) -> None:
    with self._lock:
      subscribers = self._subscribers.get(session_id)
      if not subscribers:
        return
      subscribers.discard(queue)
      if not subscribers:
        self._subscribers.pop(session_id, None)

  def recent_events(self, session_id: str) -> list[dict[str, Any]]:
    with self._lock:
      return list(self._history.get(session_id, ()))

  def snapshots(self) -> dict[str, dict[str, Any]]:
    with self._lock:
      session_ids = set(self._history.keys()) | set(self._subscribers.keys())
      return {
          session_id: {
              "subscribers": len(self._subscribers.get(session_id, set())),
              "eventsBuffered": len(self._history.get(session_id, ())),
          }
          for session_id in sorted(session_ids)
      }


def dequeue_with_timeout(queue: Queue[dict[str, Any] | None],
                         timeout_seconds: float) -> dict[str, Any] | None:
  try:
    return queue.get(timeout=timeout_seconds)
  except Empty:
    return None

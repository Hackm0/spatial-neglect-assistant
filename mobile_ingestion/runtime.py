from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import TypeVar


T = TypeVar("T")


class AsyncioRunner:

  def __init__(self) -> None:
    self._loop: asyncio.AbstractEventLoop | None = None
    self._thread: threading.Thread | None = None
    self._ready_event = threading.Event()
    self._lock = threading.Lock()

  @property
  def thread_name(self) -> str | None:
    return self._thread.name if self._thread else None

  def start(self) -> None:
    with self._lock:
      if self._thread and self._thread.is_alive():
        return
      self._ready_event.clear()
      self._thread = threading.Thread(
          target=self._run_event_loop,
          name="mobile-ingestion-asyncio",
          daemon=True,
      )
      self._thread.start()
    self._ready_event.wait(timeout=5.0)
    if self._loop is None:
      raise RuntimeError("Async runtime failed to start.")

  def run(self, coroutine: asyncio.coroutines.Coroutine[object, object, T],
          timeout: float | None = None) -> T:
    self.start()
    assert self._loop is not None
    future: Future[T] = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
    return future.result(timeout=timeout)

  def stop(self, timeout: float = 5.0) -> None:
    with self._lock:
      loop = self._loop
      thread = self._thread
    if loop is None or thread is None:
      return

    async def _shutdown_loop() -> None:
      current_task = asyncio.current_task()
      pending_tasks = [
          task for task in asyncio.all_tasks()
          if task is not current_task and not task.done()
      ]
      for task in pending_tasks:
        task.cancel()
      if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    future = asyncio.run_coroutine_threadsafe(_shutdown_loop(), loop)
    try:
      future.result(timeout=timeout)
    finally:
      loop.call_soon_threadsafe(loop.stop)
      thread.join(timeout=timeout)
      with self._lock:
        self._loop = None
        self._thread = None

  def _run_event_loop(self) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with self._lock:
      self._loop = loop
      self._ready_event.set()
    loop.run_forever()
    loop.close()

"""
Event Loop Manager for Flask Application

Manages a single persistent event loop that runs in a background thread,
ensuring all async operations (including litellm's LoggingWorker) use the same loop.
This prevents RuntimeError: Queue is bound to a different event loop.
"""
import asyncio
import threading
import atexit
from typing import Optional, Coroutine, Any


class EventLoopManager:
    """Manages a persistent event loop in a background thread."""
    
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
    
    def start(self):
        """Start the event loop in a background thread."""
        with self._lock:
            if self._running:
                return
            
            self._running = True
            
            def run_loop():
                """Run the event loop in this thread."""
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()
            
            self._thread = threading.Thread(target=run_loop, daemon=True, name="EventLoopThread")
            self._thread.start()
            
            # Wait for loop to be created
            while self._loop is None:
                threading.Event().wait(0.01)
    
    def stop(self):
        """Stop the event loop and background thread."""
        with self._lock:
            if not self._running or self._loop is None:
                return
            
            self._running = False
            
            # Schedule loop stop
            self._loop.call_soon_threadsafe(self._loop.stop)
            
            # Wait for thread to finish (with timeout)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            
            # Clean up
            if self._loop and not self._loop.is_closed():
                self._loop.close()
            
            self._loop = None
            self._thread = None
    
    def get_loop(self) -> asyncio.AbstractEventLoop:
        """Get the persistent event loop."""
        if not self._running or self._loop is None:
            raise RuntimeError("Event loop manager not started. Call start() first.")
        return self._loop
    
    def run_async(self, coro: Coroutine) -> Any:
        """
        Run an async coroutine from a sync context.
        
        Args:
            coro: The coroutine to run
            
        Returns:
            The result of the coroutine
        """
        loop = self.get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()
    
    def is_running(self) -> bool:
        """Check if the event loop manager is running."""
        return self._running and self._loop is not None


# Global instance
_manager = EventLoopManager()


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Get the persistent event loop."""
    return _manager.get_loop()


def run_async(coro: Coroutine) -> Any:
    """Run an async coroutine from a sync context using the persistent loop."""
    return _manager.run_async(coro)


def start_event_loop():
    """Start the persistent event loop."""
    _manager.start()
    # Register cleanup on exit
    atexit.register(stop_event_loop)


def stop_event_loop():
    """Stop the persistent event loop."""
    _manager.stop()


def is_event_loop_running() -> bool:
    """Check if the event loop is running."""
    return _manager.is_running()


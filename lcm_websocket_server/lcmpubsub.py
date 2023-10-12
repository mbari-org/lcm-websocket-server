"""
LCM pub/sub utilities.
"""

import lcm

import queue
import re
from threading import Thread

from lcm_websocket_server.log import get_logger
logger = get_logger(__name__)


class LCMObserver:
    """
    Observer for an LCMObservable. Puts received events in a thread-safe queue.
    """
    def __init__(self, channel_regex: str = ".*"):
        self._queue = queue.Queue()
        self._channel_regex = channel_regex
    
    def match(self, channel: str) -> bool:
        """
        Check if the observer matches a given channel.

        Args:
            channel: Channel name
        
        Returns:
            True if the observer matches the channel, False otherwise.
        """
        try:
            return re.fullmatch(self._channel_regex, channel) is not None
        except:
            return False

    def handle(self, event):
        """
        Handle an LCM event.
        """
        self._queue.put(event)
    
    def get(self, *args, **kwargs):
        """
        Gets the next event from the queue.
        """
        return self._queue.get(*args, **kwargs)


class LCMRepublisher:
    """
    Subscribes to an LCM channel in a background thread and republishes events to subscribers.
    """
    def __init__(self, channel: str):
        """
        Args:
            channel: The LCM channel to subscribe to.
        """
        self._channel = channel
        
        self._thread = Thread(target=self._run)
        self._thread.daemon = True
        self._stopped = False
        
        self._subscribers = []
    
    def subscribe(self, subscriber: LCMObserver):
        """
        Subscribes a subscriber to this observable.
        """
        self._subscribers.append(subscriber)
    
    def unsubscribe(self, subscriber: LCMObserver):
        """
        Unsubscribes a subscriber from this observable.
        """
        self._subscribers.remove(subscriber)
    
    def start(self):
        """
        Starts the LCM republisher asynchronously.
        """
        self._thread.start()
    
    def stop(self):
        """
        Stops the LCM republisher.
        """
        self._stopped = True
    
    def _run(self):
        """
        Runs the LCM subscriber handler loop.
        """
        lc = lcm.LCM()
        lc.subscribe(self._channel, self._handler)
        logger.info(f"LCM republisher subscribed to channel '{self._channel}'")
        while not self._stopped:
            lc.handle()
    
    def _handler(self, channel, data):
        """
        Handles an LCM event.
        
        Args:
            channel: The LCM channel
            data: The LCM data
        """
        for subscriber in self._subscribers:
            if subscriber.match(channel):
                subscriber.handle((channel, data))
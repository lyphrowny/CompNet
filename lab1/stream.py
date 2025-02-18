import logging
import queue
import random
import time

import attrs

from .utils import get_logger

# end of transmission seq_num
EOT = -1


@attrs.define
class Packet:
    seq_num: int
    payload: str = attrs.field(repr=lambda v: f"{v!r}")
    sent_at: float = attrs.field(default=0.0)


plog = get_logger("p")


@attrs.define
class PacketQueue:
    loss_probability: float = 0.3
    latency: float = 0.1  # in sec
    _queue: queue.Queue[Packet] = attrs.field(
        init=False,
        factory=queue.Queue,
        alias="_queue",
    )
    _rng: random.Random = attrs.field(init=False, alias="_rng")

    def __attrs_post_init__(self):
        self._rng = random.Random(42)

    def send(self, packet: Packet):
        time.sleep(self.latency)
        if self._rng.random() < self.loss_probability:
            plog.debug(f"{packet} was lost!")
        else:
            self._queue.put(packet)

    def recieve(self):
        return self._queue.get()

    def __bool__(self) -> bool:
        return not self._queue.empty()

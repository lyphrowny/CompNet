import enum
import logging
import time
from collections.abc import Iterable
from threading import Thread
from typing import Protocol

import attrs

from .go_back_n import Reciever as GBN_Reciever
from .go_back_n import Sender as GBN_Sender
from .selective_repeat import Reciever as SRW_Reciever
from .selective_repeat import Sender as SRW_Sender
from .selective_repeat_non_wiki import Reciever as SRNW_Reciever
from .selective_repeat_non_wiki import Sender as SRNW_Sender
from .stream import EOT, Packet, PacketQueue
from .utils import get_logger


def _handle_floats(
    float_or_tuple: float | tuple[float, float],
) -> tuple[float, float]:
    if not isinstance(float_or_tuple, Iterable):
        return float_or_tuple, float_or_tuple
    return float_or_tuple


def config_streams(
    loss_probability: float | tuple[float, float],
    latency: float | tuple[float, float],
) -> tuple[PacketQueue, PacketQueue]:
    s_loss_p, r_loss_p = _handle_floats(loss_probability)
    s_latency, r_latency = _handle_floats(latency)

    return PacketQueue(s_loss_p, s_latency), PacketQueue(r_loss_p, r_latency)


class SenderProto(Protocol):
    @property
    def n_sent(self) -> int: ...

    def run(self) -> None: ...


class RecieverProto(Protocol):
    @property
    def n_recieved(self) -> int: ...

    @property
    def recieved_message(self) -> str: ...

    def run(self) -> None: ...


class LowProtoEnum(enum.StrEnum):
    GBN = enum.auto()
    SRW = enum.auto()
    # SRNW = enum.auto()


hlog = get_logger("h")


@attrs.define
class HighNetProtocol:
    low_proto: LowProtoEnum
    window_size: int
    message: str
    sender_timeout: float
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    sender: SenderProto = attrs.field(init=False)
    reciever: RecieverProto = attrs.field(init=False)
    transmission_time: float = attrs.field(init=False)

    def __attrs_post_init__(self):
        match self.low_proto:
            case LowProtoEnum.GBN:
                sender_cls = GBN_Sender
                reciever_cls = GBN_Reciever
            case LowProtoEnum.SRW:
                sender_cls = SRW_Sender
                reciever_cls = SRW_Reciever
            case LowProtoEnum.SRNW:
                sender_cls = SRNW_Sender
                reciever_cls = SRNW_Reciever
        self.sender = sender_cls(
            s_to_r_stream=self.s_to_r_stream,
            r_to_s_stream=self.r_to_s_stream,
            message=self.message,
            window_size=self.window_size,
            timeout=self.sender_timeout,
        )
        self.reciever = reciever_cls(
            s_to_r_stream=self.s_to_r_stream,
            r_to_s_stream=self.r_to_s_stream,
            window_size=self.window_size,
        )

    def start_transmission(self):
        s_th = Thread(target=self.sender.run)
        r_th = Thread(target=self.reciever.run)

        start_time = time.monotonic()
        s_th.start()
        r_th.start()

        s_th.join()
        self.transmission_time = time.monotonic() - start_time
        self._close_connection()
        r_th.join()

    def _close_connection(self):
        """Emulate no_loss closing connection with no ack from reciever."""
        hlog.debug("Closing connection!")
        orig_loss = self.s_to_r_stream.loss_probability
        self.s_to_r_stream.loss_probability = 0
        self.s_to_r_stream.send(Packet(seq_num=EOT, payload="S"))
        self.s_to_r_stream.loss_probability = orig_loss
        hlog.debug("Connection closed!")

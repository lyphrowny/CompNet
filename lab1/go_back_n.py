from collections.abc import Iterable
from typing import Literal
import attrs
import random
import queue
import time
from threading import Thread

from .stream import PacketQueue, Packet, EOT
from .utils import get_logger

rlog = get_logger("r")
slog = get_logger("s")
# plog = get_logger("p")


# @attrs.define
# class Packet:
#     seq_num: int
#     payload: str


@attrs.define
class Sender:
    """Go-Back-N sender

    Say we have a message with len 10. Window size is 3.
    Split into batches: [1, 2, 3], [4, 5, 6], [7, 8, 9], [10]
    The seq_mod should be > window_size, otherwise when all packets are lost,
    one cannot determine, whether the reciever lost all packets and requesting
    resending (Request 0), or all packets were delivered and the reciever
    requests a new batch (Request 0)
    seq_mod = win_size          vs.              seq_mod = win_size + 1
    0 -> ACK lost                                   0 -> ACK lost
    1 -> ACK lost                                   1 -> ACK lost
    2 -> ACK lost                                   2 -> ACK lost
    2(0) <- ACK 2 Request 0                         2(3) <- ACK 2 Request 3
    ^ should we send new batch? should we repeat?
            it's clear, that new batch is requested ^
    Then, the seq_nums will be [0, 1, 2, 3], [0, 1, 2, 3], [0, 1]
    So, `seq_num` is the position in a batch. Can be calculated as
    `message_pos % seq_mod`

    The window is used solely to send `window_size` packets not waiting for ACK
    from reciever. We send packets while we can (n_sent packets < window_size).
    Then, update the left_bound on diff between recieved Request and left_bound
        This allows to move left_bound even when intermediate ACKs were lost
        0 - lost, 1 - lost, 2 - recieved ACK -> left_bound += 3
    Don't update `m_pos` as it points at the not yet sent packet
    left bound     left bound
    v              v
    |0 1 2| 3 -> 0 |1 2 3|
       ^            ^
       message pos  message pos

    On each iteration check for packet from reciever. Got one? Move the `left_bound`
    Now we can send another packet (unless we sent all the packets and waiting for
    any of their ACKs). On each send update `m_pos`.
    If timed out, resend the message from `left_bound`.
    """

    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    message: str
    window_size: int = 10
    timeout: float = 1.0
    n_sent: int = attrs.field(init=False, default=0)
    num_packets: int = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.num_packets = len(self.message)

    def run(self):
        seq_mod = self.window_size + 1
        left_bound = 0  # left boundary of the window
        m_pos = 0  # message position
        last_send_time = 0
        while left_bound < self.num_packets:
            if self.r_to_s_stream:
                packet = self.r_to_s_stream.recieve()
                slog.debug(f"Recieved ACK for {packet.seq_num}")
                slog.debug(
                    f"Window before update {self.message[left_bound : left_bound + self.window_size]!r}"
                )
                # we can ignore "casting" left_bound to [0, seq_mod - 1],
                # because (a-b)%c = a%c - b%c (assuming, -b%c is within [0, seq_mod - 1])
                # (a-b%c)%c === (a-b)%c
                # calculate the difference between recieved seq_num and current left_bound
                # then move the left_bound so that it "points" at the next yet unACKed packet
                left_bound += (packet.seq_num - left_bound) % seq_mod
                slog.debug(
                    f"Window after update {self.message[left_bound : left_bound + self.window_size]!r}"
                )

            if m_pos < min(left_bound + self.window_size, self.num_packets):
                packet = Packet(
                    seq_num=m_pos % seq_mod,
                    payload=self.message[m_pos],
                )
                slog.debug(f"Sent {packet}")
                self.s_to_r_stream.send(packet)
                m_pos += 1
                self.n_sent += 1
                last_send_time = time.monotonic_ns()

            if time.monotonic_ns() - last_send_time > self.timeout:
                slog.debug(
                    f"Timed out, resending from {left_bound} ({self.message[left_bound]})"
                )
                m_pos = left_bound


@attrs.define
class Reciever:
    """Go-Back-N reciever

    Recieve packets and send ACKs for recieved ones.
    The sent ACK tells the sender which seq_num packet
    we're expecting, not the one we've successfully recieved
    """

    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    window_size: int = 10
    n_recieved: int = attrs.field(init=False, default=0)

    def run(self):
        seq_mod = self.window_size + 1
        expected_seq_num = 0
        message = ""

        n_right = 0
        n_wrong = 0

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()

            # transfer ended
            if packet.seq_num == EOT:
                break

            self.n_recieved += 1
            if packet.seq_num == expected_seq_num:
                n_right += 1
                rlog.debug(f"Recieved {packet}")
                message += packet.payload
                rlog.debug(
                    f"Sent ACK {expected_seq_num} Request {(expected_seq_num + 1) % seq_mod}"
                )
                expected_seq_num = (expected_seq_num + 1) % seq_mod
            else:
                n_wrong += 1
                rlog.debug(f"Ignoring {packet}: was expecting {expected_seq_num}")
                rlog.debug(f"Sending Request {expected_seq_num}")
            rlog.debug(f"Current message {message!r}")
            self.r_to_s_stream.send(
                Packet(seq_num=expected_seq_num % seq_mod, payload="ACK")
            )

        rlog.debug(f"Recieved message: {message}")
        rlog.debug(f"{n_right = }")
        rlog.debug(f"{n_wrong = }")

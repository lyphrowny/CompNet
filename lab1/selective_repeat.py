import logging
import time
from collections import deque
from collections.abc import MutableSequence

import attrs

from .stream import EOT, Packet, PacketQueue
from .utils import get_logger

slog = get_logger("sw")
rlog = get_logger("rw")


@attrs.define
class Sender:
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    message: str
    window_size: int = 10
    timeout: float = 1.0
    n_sent: int = attrs.field(init=False, default=0)

    def run(self):
        left_bound = 0
        m_pos = 0
        seq_mod = self.window_size * 2
        _buffer: deque[Packet] = deque()
        num_packets = len(self.message)

        def fill_buffer(num_new_packets: int):
            # don't know why others are file without nonlocal
            # but m_pos not
            nonlocal m_pos

            # add either `num_new_packets` or what's left to transmit
            for _ in range(min(num_new_packets, num_packets - m_pos)):
                _buffer.append(
                    Packet(
                        seq_num=m_pos % seq_mod,
                        payload=self.message[m_pos],
                        # hack: this way all newly created messages
                        # will be sent as if their timeout was exhausted
                        sent_at=-time.monotonic(),
                    )
                )
                m_pos += 1

            # shrink buffer so that it's either `window_size` or what's left to get ACK from
            while len(_buffer) > min(self.window_size, num_packets - left_bound):
                _buffer.popleft()

        # init buffer
        fill_buffer(self.window_size)
        slog.debug(f"Filled buffer {_buffer}")

        while left_bound < num_packets:
            if self.r_to_s_stream:
                rpacket = self.r_to_s_stream.recieve()
                slog.debug(
                    f"Recieved ACK Request {rpacket.seq_num}, {rpacket.payload!r}, {rpacket}"
                )
                diff = (rpacket.seq_num - left_bound) % seq_mod
                slog.debug(f"left_bound before {left_bound}, {diff = }")
                left_bound += diff
                slog.debug(f"left_bound after {left_bound}")
                # add new packets, remove ACKed packets
                fill_buffer(diff)
                slog.debug(f"Refilled buffer {_buffer}")

            for packet in _buffer:
                # retransmit lost packets or transmit new packets for the 1st time
                if packet.sent_at + self.timeout < time.monotonic():
                    self.n_sent += 1
                    packet.sent_at = time.monotonic()
                    slog.debug(f"Sent packet {packet}")
                    self.s_to_r_stream.send(packet)


@attrs.define
class Reciever:
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    window_size: int = 10
    n_recieved: int = attrs.field(init=False, default=0)
    recieved_message: str = attrs.field(init=False, default="")

    def run(self):
        seq_mod = self.window_size * 2
        b_mod = self.window_size
        _buffer: MutableSequence[Packet | None] = [None] * b_mod
        expected_seq_num = 0
        no_ack_sent = -1
        no_ack_timeout = 0.2
        no_ack_time_sent = time.monotonic()

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()
            rlog.debug(f"Recieved packet {packet}")

            if packet.seq_num == EOT:
                break

            self.n_recieved += 1
            if packet.seq_num == expected_seq_num:
                self.recieved_message += packet.payload
                expected_seq_num = (expected_seq_num + 1) % seq_mod

                rlog.debug(f"Buffer before removal {_buffer}")
                while _buffer[expected_seq_num % b_mod] is not None:
                    self.recieved_message += _buffer[expected_seq_num % b_mod].payload
                    _buffer[expected_seq_num % b_mod] = None
                    expected_seq_num = (expected_seq_num + 1) % seq_mod
                rlog.debug(f"Buffer after removal {_buffer}")
            # if packet num is within the window
            elif (packet.seq_num - expected_seq_num) % seq_mod < b_mod:
                rlog.debug(f"Inserting new packet, before {_buffer}")
                _buffer[packet.seq_num % b_mod] = packet
                rlog.debug(f"Inserted new packet, after {_buffer}")
            else:
                rlog.debug("Out of window packet, ignoring")

            if (
                no_ack_sent != expected_seq_num
                or no_ack_time_sent + no_ack_timeout < time.monotonic()
            ):
                no_ack_sent = expected_seq_num
                no_ack_time_sent = time.monotonic()
                rlog.debug(f"Sent ACK Request {expected_seq_num}")
                self.r_to_s_stream.send(
                    Packet(
                        seq_num=expected_seq_num,
                        payload=f"REQUEST {expected_seq_num}",
                    )
                )

        rlog.debug(f"{self.recieved_message = }")

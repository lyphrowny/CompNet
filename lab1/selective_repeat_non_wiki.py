import time
from collections import deque
from collections.abc import MutableSequence

import attrs

from .stream import EOT, Packet, PacketQueue
from .utils import get_logger

slog = get_logger("snw")
rlog = get_logger("rnw")


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

        def fill_buffer():
            # don't know why others are file without nonlocal
            # but m_pos not
            nonlocal m_pos

            # fill buffer up-to-capacity or add what's left to transmit
            for _ in range(min(self.window_size - len(_buffer), num_packets - m_pos)):
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

        # init buffer
        fill_buffer()
        slog.debug(f"Filled buffer {_buffer}")

        while left_bound < num_packets:
            if self.r_to_s_stream:
                packet = self.r_to_s_stream.recieve()
                slog.debug(f"Recieved ACK {packet.seq_num}")
                diff = (packet.seq_num - left_bound) % seq_mod

                # the ACK for base or any other packet was lost,
                # but the reciever has the full window and now expects
                # a new one
                if diff == self.window_size or diff + left_bound >= num_packets:
                    # mark all the packets as ACKed
                    for packet in _buffer:
                        packet.sent_at = 2 * time.monotonic()
                elif diff > self.window_size:
                    slog.error(f"{diff} > {self.window_size} - shouldn't happen")
                else:
                    if len(_buffer) > diff:
                        # mark as ACKed
                        _buffer[diff].sent_at = 2 * time.monotonic()

                # remove consequtive ACKed packets
                while _buffer and _buffer[0].sent_at > time.monotonic():
                    _buffer.popleft()
                    left_bound += 1

                # add new packets
                fill_buffer()
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

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()
            rlog.debug(f"Recieved packet {packet}")

            if packet.seq_num == EOT:
                break

            self.n_recieved += 1
            # if packet num is within the window
            if (packet.seq_num - expected_seq_num) % seq_mod < b_mod:
                rlog.debug(f"Inserting new packet, before {_buffer}")
                _buffer[packet.seq_num % b_mod] = packet
                rlog.debug(f"Inserted new packet, after {_buffer}")

                rlog.debug(f"Sent ACK {packet.seq_num}")
                self.r_to_s_stream.send(Packet(seq_num=packet.seq_num, payload="ACK"))

                rlog.debug(f"Buffer before removal {_buffer}")
                while _buffer[expected_seq_num % b_mod] is not None:
                    self.recieved_message += _buffer[expected_seq_num % b_mod].payload
                    _buffer[expected_seq_num % b_mod] = None
                    expected_seq_num = (expected_seq_num + 1) % seq_mod
                rlog.debug(f"Buffer after removal {_buffer}")

            else:
                rlog.debug("Out of window packet, ignoring")
                rlog.debug(f"Sent ACK Request {expected_seq_num}")
                self.r_to_s_stream.send(
                    Packet(
                        seq_num=expected_seq_num,
                        payload="REQUEST",
                    )
                )

        rlog.debug(f"{self.recieved_message = }")

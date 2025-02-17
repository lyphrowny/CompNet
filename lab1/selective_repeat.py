from collections.abc import MutableSequence
import attrs
from collections import deque
import time

from .stream import PacketQueue, Packet, EOT


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
        seq_num = 0
        seq_mod = self.window_size * 2
        # repeat_from = 0
        # once deque is full and we append, leftmost element
        # will be removed
        _buffer: deque[Packet] = deque(maxlen=self.window_size)
        num_packets = len(self.message)

        def fill_buffer(num_new_packets: int):
            nonlocal m_pos, seq_mod
            for _ in range(num_new_packets):
                _buffer.append(
                    Packet(
                        seq_num=m_pos % seq_mod,
                        payload=self.message[m_pos],
                        # hack: this way all newly created messages
                        # will be sent as if their timeout was exhausted
                        sent_at=2 * time.monotonic_ns(),
                    )
                )
                m_pos += 1

        # init buffer
        fill_buffer(self.window_size)

        while left_bound < num_packets:
            if self.r_to_s_stream:
                packet = self.r_to_s_stream.recieve()
                diff = (packet.seq_num - left_bound) % seq_mod
                left_bound += diff
                # add new packets, remove ACKed packets
                fill_buffer(diff)
                # for _ in range(diff):
                #     _buffer.append(
                #         Packet(
                #             seq_num=m_pos % seq_mod,
                #             payload=self.message[m_pos],
                #             # hack: this way all newly created messages
                #             # will be sent as if their timeout was exhausted
                #             sent_at=2 * time.monotonic_ns(),
                #         )
                #     )
                #     m_pos += 1

            for packet in _buffer:
                # retransmit lost packets or transmit new packets for the 1st time
                if packet.sent_at + self.timeout > time.monotonic_ns():
                    self.n_sent += 1
                    self.s_to_r_stream.send(packet)
                    packet.sent_at = time.monotonic_ns()


@attrs.define
class Reciever:
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    window_size: int = 10
    n_recieved: int = attrs.field(init=False, default=0)

    def run(self):
        seq_mod = self.window_size * 2
        b_mod = self.window_size
        _buffer: MutableSequence[Packet | None] = [None] * b_mod
        message = ""
        expected_seq_num = 0

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()

            if packet.seq_num == EOT:
                break

            self.n_recieved += 1
            if packet.seq_num == expected_seq_num:
                expected_seq_num = (expected_seq_num + 1) % seq_mod

                while _buffer[expected_seq_num % b_mod] is not None:
                    message += _buffer[expected_seq_num % b_mod].payload
                    _buffer[expected_seq_num % b_mod] = None
                    expected_seq_num = (expected_seq_num + 1) % seq_mod
            # if packet num is within the window
            elif (packet.seq_num - expected_seq_num) % seq_mod < b_mod:
                _buffer[packet.seq_num % b_mod] = packet
            else:
                print("ignore")

            self.r_to_s_stream.send(
                Packet(
                    seq_num=expected_seq_num,
                    payload="REQUEST",
                )
            )

        print(f"{message = }")

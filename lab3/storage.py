import copy
import enum
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from functools import wraps
from itertools import batched
from queue import Queue
from threading import Thread
from typing import ClassVar, Literal, NewType, Self, cast, override

import attrs

from .go_back_n import Receiver, Sender
from .network import NodeProto
from .stream import PacketQueue as Stream

# EOT = -1

Addr = NewType("Addr", str)
UID = NewType("UID", str)
SStream = NewType("SStream", Stream)
RStream = NewType("RStream", Stream)


@attrs.define
class AddressMapper:
    """A global addr-to-stream hub."""

    address_to_stream: ClassVar[MutableMapping[Addr, Stream]]

    @staticmethod
    def _create_ptp_addr(
        t_uid: UID,
        p_uid: UID,
        stream_type: Literal["s", "r"],
    ) -> Addr:
        return cast(Addr, f"{t_uid}-{p_uid}_{stream_type}")

    @staticmethod
    def _create_ptp_addrs(
        t_uid: UID,
        p_uid: UID,
    ) -> tuple[Addr, Addr]:
        return (
            AddressMapper._create_ptp_addr(t_uid, p_uid, "s"),
            AddressMapper._create_ptp_addr(t_uid, p_uid, "r"),
        )

    @classmethod
    def add_streams(cls, peer1_uid: UID, peer2_uid: UID) -> None:
        r"""Add send and receive streams for peers.

        2 streams will be created: a SendStream from peer1 to peer2
        and a ReceiveStream from peer2 to peer1, thus forming a one-way connection.

        For a--b to have a duplex connection, one has to form 2 one-way connections
        = call this function 2 times: (a, b) and (b, a). This is unfortunate, but
        the Receiver and Sender were implemented as separate instances, which listen
        to different channels. If Receiver and Sender were merged, they could use
        only one stream.
        """
        s_to_r_addr, r_to_s_addr = AddressMapper._create_ptp_addrs(peer1_uid, peer2_uid)
        cls.address_to_stream[s_to_r_addr] = SStream()
        cls.address_to_stream[r_to_s_addr] = RStream()

    @classmethod
    def get(cls, t_uid: UID, p_uid: UID) -> tuple[SStream, RStream]:
        """Return streams for one-way connection from transmitter to peer."""
        ttp, ptt = AddressMapper._create_ptp_addrs(t_uid, p_uid)
        return cls.address_to_stream[ttp], cls.address_to_stream[ptt]


class Action(enum.IntEnum):
    # store given data
    STORE = enum.auto()
    # give stored data
    GIVE = enum.auto()
    # add peer
    ADD = enum.auto()
    # # do nothing, just relay
    # RELAY = enum.auto()
    # terminate transmitter
    TERM = enum.auto()


@attrs.frozen
class Packet:
    """A high-level packet.

    So seq_num is not needed here, but is needed on a transmission level
    """

    # sequence num
    # seq: int
    # what to do with this packet
    action: Action = attrs.field(converter=Action, repr=lambda v: v.value)
    # to whom this packet is for
    receiver_uid: UID
    # other stuff
    payload: str

    @classmethod
    def from_string(cls, string: str) -> Self:
        return cast(Self, eval(string))


@attrs.define
class Peer:
    uid: UID
    # a receiver stream which the peer listens on
    # ttp stands for transmitter-to-peer
    # send stream, because transmitter is the main role,
    # so it sends to peer
    ttp_stream: SStream
    ptt_stream: RStream
    sender: Sender
    receiver: Receiver
    ths: Iterable[Thread] = attrs.field(init=False)

    @classmethod
    def from_payload(cls, t_uid: UID, payload: str, received_packets: Queue[Packet]):
        p_uid = cast(UID, payload[:4])
        ttp_stream, ptt_stream = AddressMapper.get(t_uid, p_uid)
        sender = Sender(ttp_stream, ptt_stream)
        receiver = Receiver(ttp_stream, ptt_stream, received_packets)
        return cls(p_uid, ttp_stream, ptt_stream, sender, receiver)

    def start(self):
        self.ths = [Thread(target=self.sender.run), Thread(target=self.receiver.run)]
        for th in self.ths:
            th.start()

    def terminate(self):
        self.sender.send_termination_packet()
        for th in self.ths:
            th.join()

    def send_packet(self, packet: Packet):
        self.sender.send_packet(packet)

    # def get_packet(self) -> Packet | None:
    #     if not self.received_packets.empty():
    #         return self.received_packets.get()
    #     return None


def if_for_me(func):
    @wraps
    def wrapper(self: Transmitter, packet: Packet, *a, **kw):
        if packet.receiver_uid == self.uid:
            func(self, packet, *a, **kw)
        else:
            self._relay(packet)

    return wrapper


@attrs.frozen
class TransmitterNode:
    uid: UID
    latency: float

    @staticmethod
    def dist(a: "TransmitterNode", b: "TransmitterNode") -> float:
        return a.latency + b.latency


@attrs.define
class Transmitter:
    uid: UID
    peers: MutableMapping[UID, Peer]
    storage: "Storage"
    _should_terminate: bool = attrs.field(init=False, default=False)
    _packet_queue: Queue[Packet] = attrs.field(factory=Queue)

    def run(self):
        while not self._should_terminate:
            if not self._packet_queue.empty():
                self._receive_and_act()

    def _receive_and_act(self):
        packet = self._packet_queue.get()
        match packet.action:
            case Action.STORE:
                self._store(packet)
            case Action.GIVE:
                self._give(packet)
            case Action.ADD:
                self._add_peer(packet)
            case Action.TERM:
                self._terminate(packet)

    # def get_packet(self):
    #     for peer in self.peers.values():
    #         if peer.

    # def has_incoming_packets(self):
    #     for peer in self.peers.values():
    #         if peer.
    # def receive(self):
    #     while self.has_unread_packets():
    #         packet: Packet = self.read_packet()

    #         match packet.action:
    #             case Action.STORE:
    #                 self._store(packet)
    #             case Action.GIVE:
    #                 self._give(packet)
    #             case Action.ADD:
    #                 self._add_peer(packet)
    # case Action.RELAY:
    #     self._relay(packet)

    @if_for_me
    def _store(self, packet: Packet):
        self.storage.store(packet.payload)
        # if packet.receiver_uid == self.uid:
        #     self.storage.store(packet.payload)
        # else:
        #     self.retransmit(packet)

    def _relay(self, packet: Packet):
        peer_uid = packet.receiver_uid
        peer = self.peers.get(peer_uid, None)
        if peer is None:
            self._send_to_all_but_one(peer_uid, packet)
        else:
            self.send(peer_uid, packet)

    def _send_to_all_but_one(self, exclude_peer_uid: UID, packet: Packet):
        for peer_uid in self.peers.keys() - exclude_peer_uid:
            self.send(peer_uid, packet)

    def _give(self, packet: Packet):
        if packet.receiver_uid == self.uid:
            sender_uid = cast(UID, packet.payload[:4])
            packet = Packet(
                Action.STORE,
                sender_uid,
                self.storage.give(),
            )
            self.send(sender_uid, packet)
        else:
            packet = attrs.evolve(packet, payload=f"{self.uid}{packet.payload}")
            self._relay(packet)

    @if_for_me
    def _add_peer(self, packet: Packet):
        # if packet.receiver_uid == self.uid:
        peer = Peer.from_payload(self.uid, packet.payload, self._packet_queue)
        peer.start()
        self.peers[peer.uid] = peer
        # else:
        #     self.retransmit(packet)

    @if_for_me
    def _terminate(self, packet: Packet):
        self._should_terminate = True

        for peer in self.peers.values():
            peer.terminate()

        # close all the threads for sender and receiver

    # def send(self, peer_uid: UID, action, receiver_uid, payload):
    #     peer = self.peers[peer_uid]
    #     _send(peer.stream, action, receiver_uid, payload)

    def send(self, peer_uid: UID, packet: Packet):
        peer = self.peers[peer_uid]
        peer.send_packet(packet)


# end of stored data
EOS = str(b"\0")


@attrs.define
class Storage:
    stored_data: str = attrs.field(init=False)
    _stored_iterator: batched[str] = attrs.field(init=False)
    _batch_size: int = attrs.field(init=False, default=10)

    def store(self, data: str) -> None:
        """Store data until it has EOS, then init iterator."""
        self.stored_data += data
        if self.stored_data.endswith(EOS):
            self._stored_iterator = batched(self.stored_data, self._batch_size)

    def give(self) -> str:
        try:
            return "".join(next(self._stored_iterator))
        except StopIteration:
            return EOS


# @attrs.define
# class StorageNode(NodeProto):
#     idx: int
#     latency: float
#     storage: str = attrs.field(init=False)
#     _storage_iterator: Iterator[str] = attrs.field(init=False, default=None)

#     @override
#     def __hash__(self) -> int:
#         return hash(self.idx)

#     @override
#     def __eq__(self, other: object) -> bool:
#         if not isinstance(other, "StorageNode"):
#             raise NotImplementedError
#         return self.idx == other.idx

#     @override
#     def __lt__(self, other: object) -> bool:
#         if not isinstance(other, "StorageNode"):
#             raise NotImplementedError
#         return self.idx < other.idx

#     @override
#     @staticmethod
#     def dist(a: object, b: object) -> float:
#         if not isinstance(a, "StorageNode"):
#             raise NotImplementedError
#         if not isinstance(b, "StorageNode"):
#             raise NotImplementedError
#         return a.latency + b.latency

#     def next_chunk(self):
#         if self._storage_iterator is None:
#             self._storage_iterator = iter(self.storage)
#         try:
#             return next(self._storage_iterator)
#         except StopIteration:
#             return EOT

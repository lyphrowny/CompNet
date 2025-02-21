from collections.abc import Iterator, Mapping, MutableMapping
import copy
import enum
from functools import wraps
from itertools import batched
from typing import NewType, cast, override, ClassVar
import attrs

from lab3.network import NodeProto

EOT = -1

Addr = NewType("Addr", str)
UID = NewType("UID", str)


@attrs.define
class AddressMapper:
    address_to_stream: ClassVar[MutableMapping[Addr, Stream]]

    @classmethod
    def add_stream(cls, addr: Addr):
        cls.address_to_stream[addr] = Stream()

    @classmethod
    def get(cls, addr: Addr):
        return cls.address_to_stream[addr]


class Action(enum.IntEnum):
    # store given data
    STORE = enum.auto()
    # give stored data
    GIVE = enum.auto()
    # add peer
    ADD = enum.auto()
    # # do nothing, just relay
    # RELAY = enum.auto()


@attrs.frozen
class Packet:
    """A high-level packet.

    So seq_num is not needed here, but is needed on a transmission level
    """

    # sequence num
    # seq: int
    # what to do with this packet
    action: Action
    # to whom this packet is for
    receiver_uid: UID
    # other stuff
    payload: str


@attrs.define
class Peer:
    uid: UID
    addr: Addr
    stream: Stream

    @classmethod
    def from_payload(cls, payload: str):
        uid = cast(UID, payload[:4])
        addr = cast(Addr, payload[4:])
        stream = AddressMapper.get(addr)
        return cls(uid, addr, stream)


def if_for_me(func):
    @wraps
    def wrapper(self: Transmitter, packet: Packet, *a, **kw):
        if packet.receiver_uid == self.uid:
            func(self, packet, *a, **kw)
        else:
            self._relay(packet)

    return wrapper


@attrs.define
class Transmitter:
    uid: UID
    sender: ...
    receiver: ...
    peers: MutableMapping[UID, Peer]
    storage: ...

    def receive(self):
        while self.receiver.packets:
            packet: Packet = self.receiver.get()

            match packet.action:
                case Action.STORE:
                    self._store(packet)
                case Action.GIVE:
                    self._give(packet)
                case Action.ADD:
                    self._add_peer(packet)
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
            self._send_to_all(packet)
        else:
            self.send(peer_uid, packet)

    def _send_to_all(self, packet: Packet):
        for peer_uid in self.peers:
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
        peer = Peer.from_payload(packet.payload)
        self.peers[peer.uid] = peer
        # else:
        #     self.retransmit(packet)

    # def send(self, peer_uid: UID, action, receiver_uid, payload):
    #     peer = self.peers[peer_uid]
    #     _send(peer.stream, action, receiver_uid, payload)

    def send(self, peer_uid: UID, packet: Packet):
        peer = self.peers[peer_uid]
        _send(peer.stream, packet)


# end of stored data
EOS = b"\0"


@attrs.define
class Storage:
    stored_data: str = attrs.field(init=False)
    _stored_iterator: batched[str] = attrs.field(init=False)
    _batch_size: int = attrs.field(init=False, default=10)

    def store(self, data: str):
        """Store data until it has EOS, then init iterator."""
        self.stored_data += data
        if self.stored_data.endswith(str(EOS)):
            self._stored_iterator = batched(self.stored_data, self._batch_size)

    def give(self):
        try:
            return next(self._stored_iterator)
        except StopIteration:
            return EOS


@attrs.define
class StorageNode(NodeProto):
    idx: int
    latency: float
    storage: str = attrs.field(init=False)
    _storage_iterator: Iterator[str] = attrs.field(init=False, default=None)

    @override
    def __hash__(self) -> int:
        return hash(self.idx)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, "StorageNode"):
            raise NotImplementedError
        return self.idx == other.idx

    @override
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, "StorageNode"):
            raise NotImplementedError
        return self.idx < other.idx

    @override
    @staticmethod
    def dist(a: object, b: object) -> float:
        if not isinstance(a, "StorageNode"):
            raise NotImplementedError
        if not isinstance(b, "StorageNode"):
            raise NotImplementedError
        return a.latency + b.latency

    def next_chunk(self):
        if self._storage_iterator is None:
            self._storage_iterator = iter(self.storage)
        try:
            return next(self._storage_iterator)
        except StopIteration:
            return EOT

import enum
import attrs
from typing import NewType, Self, cast

UID = NewType("UID", str)


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
    # from whom this packet is
    from_uid: UID
    # other stuff
    payload: str

    @classmethod
    def from_string(cls, string: str) -> Self:
        return cast(Self, eval(string))

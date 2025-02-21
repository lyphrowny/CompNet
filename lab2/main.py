from collections.abc import Iterable, Mapping, MutableSequence
import enum
from itertools import product
from pathlib import Path

import attrs
from network import FilePrinter, Network, Point
import math


class NetworkType(enum.StrEnum):
    L = enum.auto()
    C = enum.auto()
    S = enum.auto()


def linear(n_pts: int = 5) -> MutableSequence[Point]:
    return [Point(i, i) for i in range(n_pts)]


def circular(
    n_pts: int = 10,
    r: float = 3,
) -> MutableSequence[Point]:
    return [
        Point(
            math.cos(2 * math.pi * i / n_pts) * r,
            math.sin(2 * math.pi * i / n_pts) * r,
        )
        for i in range(n_pts)
    ]


def starlike(n_grid: int = 3) -> MutableSequence[Point]:
    return [Point(i, j) for i, j in product(range(n_grid), repeat=2)]


def generate_points(
    net_type: NetworkType,
    **kw: Mapping[str, int | float],
) -> MutableSequence[Point]:
    match net_type:
        case NetworkType.L:
            return linear(**kw)
        case NetworkType.C:
            return circular(**kw)
        case NetworkType.S:
            return starlike(**kw)


@attrs.define
class Analysis:
    net_type: NetworkType
    node_idx_to_remove: int
    max_distance: float
    output_dir: Path = attrs.field(default=None)
    pts: MutableSequence[Point] = attrs.field(init=False)

    def __attrs_post_init__(self):
        if self.output_dir is None:
            self.output_dir = Path(__file__).parent / "res"
            self.output_dir.mkdir(exist_ok=True, parents=True)
        self.pts = generate_points(self.net_type)

    def run(self):
        net = Network.from_points(self.pts, self.max_distance)
        net.ospf(FilePrinter(self.output_dir / f"{self.net_type}"))
        # net.ospf()
        del self.pts[self.node_idx_to_remove]
        net = Network.from_points(self.pts, self.max_distance)
        # net.ospf()
        net.ospf(
            FilePrinter(
                self.output_dir / f"{self.net_type}_wo_{self.node_idx_to_remove}"
            )
        )


def main():
    analyses = [
        Analysis(
            NetworkType.L,
            node_idx_to_remove=2,
            max_distance=1.5,
        ),
        Analysis(
            NetworkType.C,
            node_idx_to_remove=0,
            max_distance=3,
        ),
        Analysis(
            NetworkType.S,
            node_idx_to_remove=4,
            max_distance=1.2,
        ),
    ]
    for analysis in analyses:
        analysis.run()


if __name__ == "__main__":
    main()

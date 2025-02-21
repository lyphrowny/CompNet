import enum
import math
from collections.abc import Mapping, MutableSequence
from pathlib import Path

import attrs
from network import FilePrinter, Network, Point
from plot import Plotter


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


def starlike(r: float = 3) -> MutableSequence[Point]:
    return [Point(0, 0), *circular(n_pts=5, r=r)]


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
    output_res_dir: Path = attrs.field(default=None)
    output_fig_dir: Path = attrs.field(default=None)
    pts: MutableSequence[Point] = attrs.field(init=False)

    def __attrs_post_init__(self):
        def _make_dir(output_dir: Path | None, subdir_name: str) -> Path:
            if output_dir is None:
                output_dir = Path(__file__).parent / subdir_name
                output_dir.mkdir(exist_ok=True, parents=True)
            return output_dir

        self.output_res_dir = _make_dir(self.output_res_dir, "res")
        self.output_fig_dir = _make_dir(self.output_fig_dir, "fig")
        self.pts = generate_points(self.net_type)

    def _ospf_and_plot(
        self, net: Network, fname: str, save_points: bool = True
    ) -> None:
        net.ospf(FilePrinter(self.output_res_dir / fname))
        plotted_points = Plotter().plot_points(net.pts)
        if save_points:
            plotted_points.save(self.output_fig_dir / f"{fname}_points")
        plotted_points.plot_graph(net).save(self.output_fig_dir / f"{fname}_graph")

    def run(self):
        net = Network.from_points(self.pts, self.max_distance)
        fname = f"{self.net_type}"
        self._ospf_and_plot(net, fname)

        net.remove_node(self.node_idx_to_remove)
        fname = f"{self.net_type}_wo_{self.node_idx_to_remove}"
        self._ospf_and_plot(net, fname, save_points=False)


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
            node_idx_to_remove=0,
            max_distance=3.1,
        ),
    ]
    for analysis in analyses:
        analysis.run()


if __name__ == "__main__":
    main()

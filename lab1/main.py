import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, MutableMapping
from itertools import chain, product, repeat
from pathlib import Path

import attrs
import matplotlib.pyplot as plt
from tqdm import tqdm

from .high_proto import HighNetProtocol, LowProtoEnum, config_streams


@attrs.define
class Result:
    n_sent: int
    n_recieved: int
    time_taken: float
    k: float = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.k = self.n_recieved / self.n_sent


def _form_table(
    results: Mapping[int | float, Mapping[str, Result]],
    test_param_name: str,
    const_param: int | float,
):
    t_level = 1
    t = lambda: " " * 4 * t_level
    num_protos = len(LowProtoEnum)
    sl = "\\\\"
    env_begin = (
        rf"\begin{{table}}[H]",
        rf"{t()}\centering",
        rf"{t()}\begin{{tabular}}{{|{'|'.join('c' for _ in range(num_protos * 2 + 1))}|}}",
    )

    t_level = 2
    header1 = (
        rf"{t()}\hline",
        rf"{t()}& {' & '.join(rf'\multicolumn{{2}}{{c|}}{{{proto.name}}}' for proto in LowProtoEnum)} {sl}",
        rf"{t()}\hline",
    )
    # text in math mode and escaping "_"
    timm = lambda t: rf"$\text{{{t.replace('_', r'\_')}}}$"
    header2 = (
        rf"{t()}{timm(test_param_name)} & {' & '.join(map(timm, 'tk' * num_protos))} {sl}",
        rf"{t()}\hline",
    )

    result_lines: list[str] = []
    for param, proto_results in results.items():
        extracted_results = chain.from_iterable(
            (proto_result.time_taken, proto_result.k)
            for proto_result in proto_results.values()
        )
        formatted_results = map(lambda v: f"{v:.3f}", extracted_results)
        result_lines.append(f"{t()}{param} & {' & '.join(formatted_results)} {sl}")
    result_lines.append(rf"{t()}\hline")

    t_level = 1
    caption = f"Коэфф. эффективности и времени передачи от размера окна при $p = {const_param}$"
    label = "ws"
    if test_param_name == "p":
        caption = f"Коэфф. эффективности и времени передачи от вероятности потери пакета при {timm('window_size')} = {const_param}"
        label = "p"
    env_end = (
        rf"{t()}\end{{tabular}}",
        rf"{t()}\caption{{{caption}}}",
        rf"{t()}\label{{tab:kt_depend_{label}}}",
        r"\end{table}",
    )

    print(
        "\n".join(
            map(
                "\n".join,
                (env_begin, header1, header2, result_lines, env_end),
            )
        )
    )


def _plot(
    results: Mapping[int | float, Mapping[str, Result]],
    varying_param: str,
    varying_label: str,
    output_dir: Path | None = None,
    should_show: bool = False,
    should_save: bool = True,
):
    fig, axs = plt.subplots(1, 2, figsize=(16, 8))
    param_values = results.keys()
    for (ax, ylabel, yparam), low_proto_name in product(
        zip(axs, ("коэф. эффективности", "время передачи, с"), ("k", "time_taken")),
        LowProtoEnum,
    ):
        ax.plot(
            param_values,
            [getattr(results[w][low_proto_name.name], yparam) for w in param_values],
            label=low_proto_name.upper(),
        )
        ax.set_xlabel(varying_label)
        ax.set_ylabel(ylabel)
        ax.legend()
        ax.grid()
    if should_show:
        fig.show()
    if should_save:
        if output_dir is None:
            output_dir = Path(__file__).parent / "figs"
            output_dir.mkdir(exist_ok=True)
        plt.tight_layout()
        fig.savefig(output_dir / f"{varying_param}.png")


def _vary_param(
    varying_param: str,
    varying_ws: Iterable[int],
    varying_lp: Iterable[float],
    varying_len: int,
    *,
    w_as_result_key: bool,
    varying_label: str,
    output_dir: Path | None = None,
    should_show: bool = False,
    should_save: bool = True,
):
    sender_timeout = 0.02
    # latency = 0.0003
    latency = 0
    msg_len = 100
    msg = "".join(map(chr, range(2**14, 2**14 + msg_len)))

    ws_lp = zip(varying_ws, varying_lp)

    results: MutableMapping[float | int, MutableMapping[str, Result]] = defaultdict(
        dict
    )
    for (ws, lp), proto in tqdm(
        product(ws_lp, LowProtoEnum),
        total=varying_len * len(LowProtoEnum),
    ):
        print(ws, lp, proto)
        (
            s_to_r_stream,  # sender to reciever stream
            r_to_s_stream,  # reciever to sender stream
        ) = config_streams(
            loss_probability=(lp, 0),
            # loss_probability=lp,
            latency=latency,
        )
        high_proto = HighNetProtocol(
            low_proto=proto,
            window_size=ws,
            message=msg,
            sender_timeout=sender_timeout,
            s_to_r_stream=s_to_r_stream,
            r_to_s_stream=r_to_s_stream,
        )
        high_proto.start_transmission()
        results[(lp, ws)[w_as_result_key]][proto.name] = Result(
            n_sent=high_proto.sender.n_sent,
            n_recieved=high_proto.reciever.n_recieved,
            time_taken=high_proto.transmission_time,
        )
        assert high_proto.reciever.recieved_message == msg, (
            f"Expected {msg}, got {high_proto.reciever.recieved_message}"
        )
    print(results)
    _form_table(results, varying_param, const_param=(lp, ws)[not w_as_result_key])
    _plot(
        results,
        varying_param,
        varying_label=varying_label,
        output_dir=output_dir,
        should_show=should_show,
        should_save=should_save,
    )
    return results


def _unset_debug_logging_level():
    for logger in logging.getLogger().getChildren():
        # logger.setLevel(logging.INFO)
        logger.setLevel(logging.WARNING)


def vary_window_size():
    _unset_debug_logging_level()
    # varying_ws = range(20, 41)
    # varying_ws = range(2, 4)
    varying_ws = range(2, 31)
    # varying_ws = range(10, 51, 5)
    # varying_ws = range(100, 1001, 100)
    varying_len = len(varying_ws)
    varying_lp = repeat(0.3, varying_len)
    _ = _vary_param(
        "window_size",
        varying_ws,
        varying_lp,
        varying_len,
        w_as_result_key=True,
        varying_label="размер окна",
    )


def vary_loss_probability():
    _unset_debug_logging_level()
    varying_len = 10
    varying_lp = (lp / 10 for lp in range(varying_len))
    varying_ws = repeat(3, varying_len)
    _ = _vary_param(
        "p",
        varying_ws,
        varying_lp,
        varying_len,
        w_as_result_key=False,
        varying_label="коэфф. потерь",
    )


def main():
    (
        s_to_r_stream,  # sender to reciever stream
        r_to_s_stream,  # reciever to sender stream
    ) = config_streams(
        # loss_probability=(0.3, 0.3),
        loss_probability=(0.3, 0.0),
        latency=0.0003,
    )
    _unset_debug_logging_level()

    import string
    from string import ascii_lowercase

    msg = ascii_lowercase[:5]
    msg = string.printable[:9]
    high_proto = HighNetProtocol(
        low_proto=LowProtoEnum.GBN,
        window_size=3,
        message=msg,
        sender_timeout=0.5,
        s_to_r_stream=s_to_r_stream,
        r_to_s_stream=r_to_s_stream,
    )

    high_proto.start_transmission()

    res = Result(
        n_sent=high_proto.sender.n_sent,
        n_recieved=high_proto.reciever.n_recieved,
        time_taken=high_proto.transmission_time,
    )

    print(res)


if __name__ == "__main__":
    # main()
    vary_window_size()
    vary_loss_probability()

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
import logging
from tarfile import ExtractError
import attrs
from itertools import product, chain, repeat
from collections import defaultdict
from .high_proto import HighNetProtocol, LowProtoEnum, config_streams
from tqdm import tqdm


@attrs.define
class Result:
    n_sent: int
    n_recieved: int
    time_taken: float
    k: float = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.k = self.n_recieved / self.n_sent


def _vary_param(
    varying_param: str,
    varying_ws: Iterable[int],
    varying_lp: Iterable[float],
    varying_len: int,
):
    sender_timeout = 0.2
    latency = 0.03
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
            loss_probability=lp,
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
        results[ws][proto.name] = Result(
            n_sent=high_proto.sender.n_sent,
            n_recieved=high_proto.reciever.n_recieved,
            time_taken=high_proto.transmission_time,
        )
        assert high_proto.reciever.recieved_message == msg, (
            f"Expected {msg}, got {high_proto.reciever.recieved_message}"
        )
    print(results)
    _form_table(results, varying_param)


def _form_table(
    results: Mapping[int | float, Mapping[str, Result]],
    test_param_name: str,
):
    t_level = 1
    t = lambda: "\t" * t_level
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
        rf"{t()}& {' & '.join(rf'\multicolumn{{2}}{{c|}}{proto.name}' for proto in LowProtoEnum)} {sl}",
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

    t_level = 1
    env_end = (
        rf"{t()}\end{{tabular}}",
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


def _unset_debug_logging_level():
    for logger in logging.getLogger().getChildren():
        logger.setLevel(logging.INFO)


def vary_window_size():
    _unset_debug_logging_level()
    varying_ws = range(2, 11)
    varying_len = len(varying_ws)
    varying_lp = repeat(0.3, varying_len)
    _vary_param("window_size", varying_ws, varying_lp, varying_len)


def vary_loss_probability():
    _unset_debug_logging_level()
    varying_len = 10
    varying_lp = (lp / 10 for lp in range(varying_len))
    varying_ws = repeat(3, varying_len)
    _vary_param("window_size", varying_ws, varying_lp, varying_len)


def main():
    (
        s_to_r_stream,  # sender to reciever stream
        r_to_s_stream,  # reciever to sender stream
    ) = config_streams(
        # loss_probability=(0.3, 0.3),
        loss_probability=(0.3, 0.3),
        latency=0.03,
    )

    from string import ascii_lowercase
    import string

    msg = ascii_lowercase[:5]
    msg = string.printable
    high_proto = HighNetProtocol(
        low_proto=LowProtoEnum.SRW,
        window_size=8,
        message=msg,
        sender_timeout=0.2,
        s_to_r_stream=s_to_r_stream,
        r_to_s_stream=r_to_s_stream,
    )

    high_proto.start_transmission()

    print(f"{high_proto.transmission_time = }")
    print(f"{high_proto.sender.n_sent = }")
    print(f"{high_proto.reciever.n_recieved = }")


if __name__ == "__main__":
    # main()
    # vary_window_size()
    res = {
        2: {
            "GBN": Result(
                n_sent=504,
                n_recieved=353,
                time_taken=15.265743399970233,
            ),
            "SRW": Result(
                n_sent=192,
                n_recieved=138,
                time_taken=13.139033399987966,
            ),
        },
        3: {
            "GBN": Result(
                n_sent=501,
                n_recieved=357,
                time_taken=15.150218800175935,
            ),
            "SRW": Result(
                n_sent=255,
                n_recieved=174,
                time_taken=13.870592600200325,
            ),
        },
        4: {
            "GBN": Result(
                n_sent=503,
                n_recieved=356,
                time_taken=15.214577700011432,
            ),
            "SRW": Result(
                n_sent=240,
                n_recieved=172,
                time_taken=10.239985799882561,
            ),
        },
        5: {
            "GBN": Result(
                n_sent=486,
                n_recieved=329,
                time_taken=14.69691379996948,
            ),
            "SRW": Result(
                n_sent=277,
                n_recieved=184,
                time_taken=10.29994080006145,
            ),
        },
        6: {
            "GBN": Result(
                n_sent=508,
                n_recieved=345,
                time_taken=15.364086499903351,
            ),
            "SRW": Result(
                n_sent=262,
                n_recieved=189,
                time_taken=8.491465300088748,
            ),
        },
        7: {
            "GBN": Result(
                n_sent=489,
                n_recieved=360,
                time_taken=14.789145200047642,
            ),
            "SRW": Result(
                n_sent=289,
                n_recieved=208,
                time_taken=8.782096500042826,
            ),
        },
        8: {
            "GBN": Result(
                n_sent=508,
                n_recieved=354,
                time_taken=15.362937399884686,
            ),
            "SRW": Result(
                n_sent=445,
                n_recieved=315,
                time_taken=13.457267499994487,
            ),
        },
        9: {
            "GBN": Result(
                n_sent=515,
                n_recieved=346,
                time_taken=15.577725099865347,
            ),
            "SRW": Result(
                n_sent=955,
                n_recieved=676,
                time_taken=28.874859699979424,
            ),
        },
        10: {
            "GBN": Result(
                n_sent=490,
                n_recieved=359,
                time_taken=14.824180499883369,
            ),
            "SRW": Result(
                n_sent=2070,
                n_recieved=1451,
                time_taken=62.602456900058314,
            ),
        },
    }

    _form_table(res, "window_size")

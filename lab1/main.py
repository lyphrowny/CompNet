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


# def vary_loss_probability():
#     loss_probabilities = [lp / 10 for lp in range(10)]
#     window_size = 3
#     sender_timeout = 0.2
#     latency = 0.03
#     msg_len = 100
#     msg = "".join(map(chr, range(2**14, 2**14 + msg_len)))

#     results: MutableMapping[float, MutableMapping[str, Result]] = defaultdict(dict)
#     for lp, proto in tqdm(
#         product(loss_probabilities, LowProtoEnum),
#         total=len(loss_probabilities) * len(LowProtoEnum),
#     ):
#         (
#             s_to_r_stream,  # sender to reciever stream
#             r_to_s_stream,  # reciever to sender stream
#         ) = config_streams(
#             loss_probability=lp,
#             latency=latency,
#         )
#         high_proto = HighNetProtocol(
#             low_proto=proto,
#             window_size=window_size,
#             message=msg,
#             sender_timeout=sender_timeout,
#             s_to_r_stream=s_to_r_stream,
#             r_to_s_stream=r_to_s_stream,
#         )
#         high_proto.start_transmission()
#         results[lp][proto.name] = Result(
#             n_sent=high_proto.sender.n_sent,
#             n_recieved=high_proto.reciever.n_recieved,
#             time_taken=high_proto.transmission_time,
#         )
#         assert high_proto.reciever.recieved_message == msg, (
#             f"Expected {msg}, got {high_proto.reciever.recieved_message}"
#         )
#     print(results)
#     _form_table(results, "p")


# def vary_window_size():
#     # loss_probability = 0.3
#     # window_sizes = range(2, 11)
#     # sender_timeout = 0.2
#     # latency = 0.03
#     # msg_len = 100
#     # msg = "".join(map(chr, range(2**14, 2**14 + msg_len)))

#     # results: MutableMapping[float, MutableMapping[str, Result]] = defaultdict(dict)
#     # for ws, proto in tqdm(
#     #     product(window_sizes, LowProtoEnum),
#     #     total=len(window_sizes) * len(LowProtoEnum),
#     # ):
#     #     (
#     #         s_to_r_stream,  # sender to reciever stream
#     #         r_to_s_stream,  # reciever to sender stream
#     #     ) = config_streams(
#     #         loss_probability=loss_probability,
#     #         latency=latency,
#     #     )
#     #     high_proto = HighNetProtocol(
#     #         low_proto=proto,
#     #         window_size=ws,
#     #         message=msg,
#     #         sender_timeout=sender_timeout,
#     #         s_to_r_stream=s_to_r_stream,
#     #         r_to_s_stream=r_to_s_stream,
#     #     )
#     #     high_proto.start_transmission()
#     #     results[ws][proto.name] = Result(
#     #         n_sent=high_proto.sender.n_sent,
#     #         n_recieved=high_proto.reciever.n_recieved,
#     #         time_taken=high_proto.transmission_time,
#     #     )
#     #     assert high_proto.reciever.recieved_message == msg, (
#     #         f"Expected {msg}, got {high_proto.reciever.recieved_message}"
#     #     )
#     # print(results)
#     # _form_table(results, "window size")


def _vary_param(
    varying_param: str,
    varying_ws: Iterable[int],
    varying_lp: Iterable[float],
    varying_len: int,
):
    # loss_probability = 0.3
    # window_sizes = range(2, 11)
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
    env_begin = rf"\begin{{table}}[H]\n{t()}\centering\n{t()}\begin{{tabular}}{{|{'|'.join('c' for _ in range(num_protos * 2 + 1))}|}}\n"

    t_level = 2
    header1 = rf"{t()}\hline\n{t()}& {' & '.join(rf'\multicolumn{{2}}{{c|}}{proto.name}' for proto in LowProtoEnum)} {sl}n{t()}\hline\n"
    header2 = rf"{t()}{f'${test_param_name}$'} & {' & '.join(f'${param}$' for param in 'tk' * num_protos)} {sl}\n{t()}\hline\n"

    result_lines = []
    for param, proto_results in results.items():
        extracted_results = chain.from_iterable(
            (proto_result.time_taken, proto_result.k)
            for proto_result in proto_results.values()
        )
        formatted_results = map(lambda v: f"{v:.3f}", extracted_results)
        result_lines.append(f"{t()}{param} & {' & '.join(formatted_results)}{sl}")

    t_level = 1
    env_end = rf"{t()}\end{{tabular}}\n\end{{table}}"

    print(f"{env_begin}{header1}{header2}{'\n'.join(result_lines)}{env_end}")


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
        loss_probability=(0.2, 0.3),
        latency=0.05,
    )

    from string import ascii_lowercase

    msg = ascii_lowercase[:5]
    high_proto = HighNetProtocol(
        low_proto=LowProtoEnum.SRNW,
        window_size=3,
        message=msg,
        sender_timeout=3,
        s_to_r_stream=s_to_r_stream,
        r_to_s_stream=r_to_s_stream,
    )

    high_proto.start_transmission()

    print(f"{high_proto.transmission_time = }")
    print(f"{high_proto.sender.n_sent = }")
    print(f"{high_proto.reciever.n_recieved = }")


if __name__ == "__main__":
    vary_window_size()

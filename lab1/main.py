from .high_proto import config_streams, HighNetProtocol, LowProtoEnum


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
        low_proto=LowProtoEnum.GBN,
        window_size=3,
        message=msg,
        reciever_timeout=1,
        s_to_r_stream=s_to_r_stream,
        r_to_s_stream=r_to_s_stream,
    )

    high_proto.start_transmission()

    print(f"{high_proto.transmission_time = }")
    print(f"{high_proto.sender.n_sent = }")
    print(f"{high_proto.reciever.n_recieved = }")


if __name__ == "__main__":
    main()

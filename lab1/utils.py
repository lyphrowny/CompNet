import logging

# end of transmission seq_num
EOT = -1

log_level = logging.DEBUG


def get_logger(prefix: str):
    logger = logging.getLogger(prefix)

    # if already created and configured
    if logger.hasHandlers():
        return logger

    logger.setLevel(log_level)

    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    formatter = logging.Formatter(f"{prefix} - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

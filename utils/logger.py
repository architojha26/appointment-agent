import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def get_custom_logger(name: str, log_dir: str = "logs", level=logging.INFO):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"{name}.log"
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        ch.setFormatter(fmt)

        fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(level)
        fh.setFormatter(fmt)

        logger.addHandler(ch)
        logger.addHandler(fh)
    return logger

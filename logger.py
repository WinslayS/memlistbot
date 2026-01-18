# ============ LOGGING ============
import logging
import sys

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[37m",
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[91m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        message = super().format(record)
        return f"{color}{message}{self.RESET}"

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
handler.setFormatter(ColorFormatter("[%(levelname)s] %(message)s"))

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.handlers.clear()   # 🔥 ВАЖНО
logger.addHandler(handler)

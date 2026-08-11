"""日志系统 —— 自定义彩色终端输出。"""

import logging
import sys
import time

COLORS = {
    'DEBUG': '[90m',
    'INFO': '[0m',
    'SEND': '[92m',
    'RECV': '[94m',
    'STAT': '[96m',
    'WARN': '[93m',
    'ERROR': '[91m',
    'RESET': '[0m',
}

SEND_LEVEL = 25
RECV_LEVEL = 26
STAT_LEVEL = 27
logging.addLevelName(SEND_LEVEL, 'SEND')
logging.addLevelName(RECV_LEVEL, 'RECV')
logging.addLevelName(STAT_LEVEL, 'STAT')


class ColoredFormatter(logging.Formatter):
    def __init__(self, use_color=True):
        super().__init__(datefmt='%H:%M:%S')
        self.use_color = use_color

    def format(self, record):
        ts = time.strftime('%H:%M:%S', time.localtime(record.created))
        arrows = {
            'SEND': '->', 'RECV': '<-', 'STAT': '--',
            'WARN': '!!', 'ERROR': 'XX', 'DEBUG': '..',
        }
        arrow = arrows.get(record.levelname, '  ')
        msg = record.getMessage()
        if self.use_color:
            c = COLORS.get(record.levelname, '')
            r = COLORS['RESET']
            return f'{c}[{ts}] {arrow} {msg}{r}'
        return f'[{ts}] {arrow} {msg}'


_logger = logging.getLogger('wstool')
_logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.INFO)
_handler.setFormatter(ColoredFormatter(use_color=True))
_logger.addHandler(_handler)

log_debug = _logger.debug
log_info = _logger.info
log_send = lambda msg: _logger.log(SEND_LEVEL, msg)
log_recv = lambda msg: _logger.log(RECV_LEVEL, msg)
log_stat = lambda msg: _logger.log(STAT_LEVEL, msg)
log_warn = _logger.warning
log_error = _logger.error


def configure_logging(args):
    if args.no_color:
        _handler.setFormatter(ColoredFormatter(use_color=False))
    if args.quiet:
        _handler.setLevel(logging.WARNING)
    elif args.debug:
        _handler.setLevel(logging.DEBUG)
    else:
        _handler.setLevel(logging.INFO)

# ------------------------------------------------------------------------------
#  vkimexp [VK dialogs exporter]
#  (c) 2023 A. Shavykin <0.delameter@gmail.com>
# ------------------------------------------------------------------------------

import enum
import logging
import os
import time
from dataclasses import dataclass
from logging import Logger as BaseLogger, FileHandler, StreamHandler
from pathlib import Path
from threading import Lock

import click
from bs4 import BeautifulSoup
from urllib3.util import parse_url

DOMAIN = "vk.com"
URL = "https://" + DOMAIN + "/al_im.php"
HOST = (lambda u=parse_url(URL): u.scheme + '://' + u.host)()

PAGE_SIZE = 100


class DownloadError(Exception): ...


class MessageHandleError(Exception): ...


@dataclass(frozen=True)
class MessageDTO:
    msg_idx: int
    ts: int
    text: str
    attach_count: int
    msg_id: int
    attach: any
    inbox: bool
    from_peer_id: int | None


class Counter:
    def __init__(self, default: int = 0):
        self._value: int = default
        self._lock = Lock()

    def increment(self, times: int = 1):
        assert times >= 0
        with self._lock:
            self._value += times

    def __int__(self):
        return self._value

    @property
    def value(self) -> int:
        return self._value


@dataclass(frozen=True)
class Totals:
    msg_count_html = Counter()
    msg_count_index = Counter()
    attach_found = Counter()
    attach_downloaded = Counter()


class Context:
    _OUT_DIR = Path(__file__).parent.parent / 'out'

    def __init__(self, clctx: click.Context, peer_id: int, attempt: int):
        self.browser: str = clctx.params.get("browser")
        self.verbose: int = clctx.params.get("verbose")
        self.peer_id: int = peer_id
        self.attempt: int = attempt
        self.out_dir: Path = self._OUT_DIR / str(self.peer_id)

        self.totals = Totals()
        self.peer_name_map = PeerNameMap()

        self.max_msg_idx: int | None = None
        self.max_page: int | None = None

        self.page: int | None = None
        self.offset: int | None = None

    @property
    def out_dir_root(self) -> Path:
        return self._OUT_DIR

    @property
    def is_group_conversation(self) -> bool:
        return self.peer_id >= 2000000000

    @staticmethod
    def get_logs_dir() -> Path:
        return Context._OUT_DIR / 'logs'


class PeerNameMap(dict[int, str]):
    def add(self, soup: BeautifulSoup):
        for mstack in soup.find_all('div', attrs={'class': 'im-mess-stack'}):
            try:
                peer_id = int(mstack['data-peer'])
                pname_el = mstack.find_next('div', attrs={'class': 'im-mess-stack--pname'})
                pname = pname_el.find('a').text.strip()

                if self.get(peer_id, None) != pname:
                    get_logger().debug(f"Setting peer name {peer_id} -> {pname!r}")
                    self[peer_id] = pname
            except Exception as e:
                continue


def get_logger() -> BaseLogger:
    return logging.getLogger(__package__)


def init_logging(verbose: int):
    logger = get_logger()
    logger.setLevel(logging.DEBUG)

    stderr_level = logging.WARNING
    match verbose:
        case 1:
            stderr_level = logging.DEBUG

    stderr_hdlr = StreamHandler()
    stderr_hdlr.setLevel(stderr_level)
    stderr_hdlr.setLevel(stderr_level)
    logger.addHandler(stderr_hdlr)

    fmt = '[%(levelname)5.5s][%(name)s.%(module)s] %(message)s'
    file_fmtr = logging.Formatter(fmt)

    logs_dir = Context.get_logs_dir()
    os.makedirs(logs_dir, exist_ok=True)

    for suffix, level in zip(('app', 'err'), (logger.level, logging.ERROR)):
        file_hdlr = FileHandler(logs_dir / f'{time.time():.0f}.{suffix}.log', 'xt')
        file_hdlr.setLevel(level)
        file_hdlr.setFormatter(file_fmtr)
        logger.addHandler(file_hdlr)


class AttachmentEventTypeEnum(enum.StrEnum):
    STARTED = enum.auto()
    PARTIAL = enum.auto()
    FAILED = enum.auto()
    SUCCESS = enum.auto()

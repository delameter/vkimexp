# ------------------------------------------------------------------------------
#  vkimexp [VK dialogs exporter]
#  (c) 2023-2024 A. Shavykin <0.delameter@gmail.com>
# ------------------------------------------------------------------------------
import dataclasses
import html
import re
import shutil
import tempfile

import pytermor as pt
import json
import os.path
from abc import abstractmethod, ABCMeta
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
import typing as t

from bs4 import BeautifulSoup
from .common import Context, MessageDTO


class Writer(metaclass=ABCMeta):
    """
    Writer implementations are responsible for creating output files
    in different formats.
    """

    def __init__(self, ctx: "Context"):
        self._ctx = ctx

    @abstractmethod
    def write(self, *args, **kwargs) -> bool:
        ...

    @abstractmethod
    def close(self):
        ...


class IndexWriter(Writer):
    """
    Writes fetched history in plain text format (e.g. for quick greping).
    """

    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        self._seen_msg_idxs = set()

        now_ts = datetime.now().timestamp()
        self._index_file = open(ctx.out_dir / "index.txt", "wt")

        header = self._fmt_row("#", "|", "", int(now_ts), 0, f"INDEX FOR PEER {ctx.peer_id}")
        self._write_row(*header)
        self._index_file.write("-" * 120 + "\n")

    def write(self, dto: MessageDTO) -> bool:
        if dto.msg_idx in self._seen_msg_idxs:
            return False
        self._seen_msg_idxs.add(dto.msg_idx)

        peer_name = None
        if self._ctx.is_group_conversation:
            peer_name = self._ctx.peer_name_map.get(dto.from_peer_id, str(dto.from_peer_id))

        text = html.unescape(dto.text).replace("<br>", "\n")
        text = re.sub(R'<img class="emoji".+?alt="(.+?)".*?>\s*', r"\1", text)

        fields = self._fmt_row(dto.msg_idx, dto.inbox, peer_name, dto.ts, dto.attach_count, text)
        self._write_row(*fields)
        return True

    def _fmt_row(
        self,
        msg_idx: str | int,
        inbox: bool | str,
        peer_name: str | None,
        ts: int,
        attach_count: int,
        text: str,
    ) -> Iterable[str]:
        yield ("(" + str(msg_idx) + ")").rjust(10)
        yield datetime.fromtimestamp(ts).strftime("[%0e-%b-%y %H:%M:%S]")
        if self._ctx.is_group_conversation:
            yield "|"
            yield pt.fit(pt.cut(peer_name, 18, "<"), 18, ">")
        yield (inbox if isinstance(inbox, str) else ("<" if inbox else ">")).center(3)
        yield ["", f"[+{attach_count:d}A] "][attach_count > 0] + text

    def _write_row(self, *fields: str):
        left_parts_len = 10 + 1 + 20 + 1 + 3 + 1
        if self._ctx.is_group_conversation:
            left_parts_len += 1 + 1 + 18 + 1
        lines = [*pt.filtere(" ".join(fields).split("\n"))]
        result = ("\n" + pt.pad(left_parts_len)).join(lines)
        pt.echo(result, file=self._index_file)

    def close(self):
        if not hasattr(self, "_index_file"):
            return
        if not self._index_file.closed:
            self._index_file.close()


class JsonWriter(Writer):
    """
    Writes fetched history in machine-readable JSON format.
    """

    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        self._output_filename = ctx.out_dir / "index.json"
        self._seen_msg_idxs = set()
        self._msgs = deque[MessageDTO]()
        # now_ts = datetime.now().timestamp()
        # self._json_file = open(, "wt")

    def write(self, dto: MessageDTO) -> bool:
        """Not an actual 'write', rather buffering"""
        if dto.msg_idx in self._seen_msg_idxs:
            return False
        self._seen_msg_idxs.add(dto.msg_idx)
        self._msgs.append(dto)
        return True

    def close(self):
        """Actual writing happens here"""
        data = [dataclasses.asdict(msg) for msg in self._msgs]
        _, tmp_filename = tempfile.mkstemp(text=True)
        with open(tmp_filename, "wt") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        shutil.move(tmp_filename, self._output_filename)  # atomic write


class RawWriter(Writer):
    """
    Writes backend's responses before any processing happens; mostly for debugging purposes.
    """

    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        os.makedirs(self._get_out_subdir(), exist_ok=True)

    def write(self, html: str, data: dict, offset: int) -> bool:
        self._write_file(f"html.{offset}.txt", html)
        self._write_file(f"data.{offset}.json", json.dumps(data, ensure_ascii=False, indent=4))
        return True

    def close(self):
        pass

    def _get_out_subdir(self) -> Path:
        return self._ctx.out_dir / "raw"

    def _write_file(self, filename: str, content: str):
        local_abs_path = self._get_out_subdir() / filename
        if local_abs_path.exists():
            return local_abs_path
        with open(local_abs_path, "wt") as f:
            f.write(content)


class HtmlWriter(Writer):
    """
    Creates a set of navigable HTML pages with the history.
    """

    _MSG_PAGE_LIMIT = 1000
    HTML_HEAD = (
        f"<html>"
        f"<head>"
        f'<meta charset="utf-8">'
        f'<link rel="stylesheet" type="text/css" href="default.css">'
        f"</head>"
        f"<body>"
    )

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self._outs: deque[t.TextIO] = deque()
        self._cur_msg_count = 0

    def close(self):
        self._finalize()

    def write(self, soup: BeautifulSoup, offset: int, msg_count: int) -> bool:
        """
        Modifies `soup` param!
        """
        self._cur_msg_count += msg_count
        if self._cur_msg_count > self._MSG_PAGE_LIMIT or len(self._outs) == 0:
            self._cur_msg_count = msg_count
            out = open(self._ctx.out_dir / f"rendered{len(self._outs) + 1}.html", "wt")
            out.write(self.HTML_HEAD)
            self._outs.append(out)

        marker = f"<!-- offset={offset} --> "
        self._cur_out.write(marker + self._sanitize(soup) + "\n")
        return True

    def _sanitize(self, soup: BeautifulSoup) -> str:
        for label in soup.find_all("span", attrs={"class": "blind_label"}):
            label.decompose()
        return str(soup)

    @property
    def _cur_out(self) -> t.TextIO:
        if len(self._outs) > 0:
            return self._outs[-1]
        raise RuntimeError("No out files -- seems like a bug")

    def _finalize(self) -> None:
        for idx, out_ren in enumerate(self._outs):
            out = self._outs[idx]
            out.write("<br>")
            if idx > 0:
                out.write(f'<a href="rendered{idx}.html">&lt;&lt; Prev&nbsp;</a>')
            if idx < len(self._outs) - 1:
                out.write(f'<a href="rendered{idx + 2}.html">&nbsp;Next &gt;&gt;</a>')
            out.write("</body></html>")
            out.close()

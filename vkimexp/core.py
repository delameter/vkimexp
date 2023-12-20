import logging
import math
import os
import shutil
from time import sleep

import click

from .auth import Auth
from .common import URL, PAGE_SIZE, get_logger
from .handler import *
from .printer import StatePrinter
from .writer import *


class Task:
    MAX_INIT_ATTEMPTS = 10

    def __init__(self, clctx: click.Context, peer_id: int):
        self._ctx = Context(peer_id)
        self._auth = Auth(clctx.params.get("browser"))

        os.makedirs(self._ctx.out_dir, exist_ok=True)

        self._seen_msg_ids = set()
        self._attachment_storage = AttachmentStorage()
        self._failed_requests: deque[tuple[int, Exception]] = deque()
        self._printer = StatePrinter(self._ctx)

        self._index_writer = IndexWriter(self._ctx)
        self._raw_writer = RawWriter(self._ctx)
        self._html_writer = HtmlWriter(self._ctx)

        self._writers = [
            self._index_writer,
            self._raw_writer,
            self._html_writer,
        ]
        self._handlers: list[AttachmentHandler] = [
            ImagesHandler(self._ctx),
            PhotosHandler(self._ctx),
            AudioMsgsHandler(self._ctx),
        ]

    def run(self) -> None:
        get_logger().info(f"Starting to process PEER {self._ctx.peer_id}")

        last_page_data = None
        attempts = 0
        while last_page_data is None:
            try:
                _, data, size = self._fetch_im_data()
                last_page_data = [*self._handle_response_data(data)]
            except RuntimeError as e:
                if attempts < self.MAX_INIT_ATTEMPTS:
                    attempts += 1
                    self._printer.print_init_attempt(attempts)
                    sleep(math.log(attempts, 1.2))
                    continue
                get_logger().error(e)
                return

        max_page = -1
        if max_idx := max([dto.msg_idx for dto in last_page_data] + [0]):
            max_page = math.ceil(max_idx // PAGE_SIZE)

        self._ctx.max_msg_idx = max_idx
        self._ctx.max_page = max_page
        self._printer.print_header()

        for page in range(max_page, -2, -1):
            # page -1 is the last one, without an offset

            offset = (page * PAGE_SIZE) + 30
            if offset < 0:
                offset = 0

            self._ctx.page = page
            self._ctx.offset = offset
            self._printer.print_pre_request()

            try:
                html, data, size = self._fetch_im_data(offset)
                soup = BeautifulSoup(html, features='html.parser')
                self._ctx.peer_name_map.add(soup)
                self._raw_writer.write(html, data, offset)

                html_count_cur = self._delete_duplicates(soup)
                index_count_cur = 0
                for dto in self._handle_response_data(data):
                    if self._index_writer.write(dto):
                        index_count_cur += 1

                extra_count = html_count_cur - index_count_cur
                self._printer.print_post_request(size, index_count_cur, extra_count)

                for hdlr in self._handlers:
                    hdlr.prepare(soup)
                    hdlr.handle(soup, self._attachment_event)

                self._html_writer.write(soup, offset, html_count_cur)

                self._ctx.totals.msg_count_html.increment(html_count_cur)
                self._ctx.totals.msg_count_index.increment(index_count_cur)

            except RuntimeError as e:
                self._printer.print_failed_request(e)
                self._failed_requests.append((offset, e))
            else:
                self._printer.print_completed_request()

            sleep(0.05)

        try:
            src_css = self._ctx.out_dir_root / 'default.css'
            dst_css = self._ctx.out_dir / 'default.css'
            if not os.path.exists(dst_css):
                # os.symlink(src_css, dst_css)
                shutil.copy(src_css, dst_css)
        except Exception as e:
            get_logger().exception(e)

        for attach_idx, attach_res in self._attachment_storage.items():
            if isinstance(attach_res, Exception):
                get_logger().error(f"Attachment {attach_idx} failed: {attach_res}")
        for offset, failed_req_err in self._failed_requests:
            get_logger().error(f"Request at offset {offset} failed: {failed_req_err}")

        self._printer.print_footer()

    def _fetch_im_data(self, offset: int = 0) -> tuple[str, dict, int]:
        response = requests.get(
            URL,
            params={
                "act": "a_history",
                "al": 1,
                "gid": 0,
                "im_v": 3,
                "offset": offset,
                "peer": self._ctx.peer_id,
                "toend": 0,
                "whole": 0,
            },
            cookies=self._auth.cookies,
            headers={
                "authority": "vk.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9,bg;q=0.8,sr;q=0.7,ja;q=0.6,tg;q=0.5,zu;q=0.4,ru;q=0.3",
                "cache-control": "no-cache",
                "content-type": "application/x-www-form-urlencoded",
                "dnt": "1",
                "origin": "https://vk.com",
                "pragma": "no-cache",
                "referer": f"https://vk.com/im?sel={self._ctx.peer_id}",
                "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest",
            },
        )
        if not response.ok:
            raise RuntimeError(f"Failed to get IM data (HTTP {response.status_code})")
        get_logger().debug(f"GET {URL}: HTTP {response.status_code}")

        try:
            rendered, data, *_ = response.json()['payload'][1]
            return rendered, data, len(response.text)
        except KeyError:
            raise RuntimeError(f"Failed to read payload: {response.json():.1000s}")

    def _delete_duplicates(self, soup: BeautifulSoup) -> int:
        count = 0
        for li in soup.find_all('li', attrs={'class': 'im-mess'}):
            try:
                msg_id = int(li['data-msgid'])
            except ValueError as e:
                get_logger().warning(f"Message element without ID: {li}")
                continue

            if msg_id in self._seen_msg_ids:
                li.replace_with('')
                continue
            self._seen_msg_ids.add(msg_id)
            count += 1
        return count

    @classmethod
    def _handle_response_data(cls, data: dict | t.Any) -> t.Iterable[MessageDTO]:
        if not data:
            return
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected JSON object response, got: {data!r}")
        for _, msg in data.items():
            msg_id, flags, _2, ts, text, attach, _6, _7, num, *_ = msg
            inbox = not bool(flags & 2)
            attach_count = int(attach.get('attach_count', 0))
            from_peer_id = attach.get("from", None)

            dto = MessageDTO(num, ts, text, attach_count, msg_id, attach, inbox, from_peer_id)
            get_logger().debug(repr(dto).rstrip())
            yield dto

    def _attachment_event(self, hdlr: AttachmentHandler, idx: int, event_type: AttachmentEventTypeEnum, res: Path|Exception = None):
        type_letter = hdlr.get_type().upper()[0]
        attach_idx = f"{self._ctx.offset}:{type_letter}{idx}"

        self._attachment_storage[attach_idx] = res
        self._printer.print_attachment(type_letter, attach_idx, event_type)

        msg = f"Attachment {attach_idx}: {event_type}"
        if res:
            msg += f' [{res}]'
        get_logger().debug(msg)

    def close(self):
        for actor in self._writers:
            actor.close()


AttachmentResult = Path|Exception|None
AttachmentStorage = dict[str, AttachmentResult]

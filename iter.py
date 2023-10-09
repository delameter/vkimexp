import json
import math
import operator
import re
import shutil
import urllib.parse
from abc import abstractmethod, ABCMeta
from datetime import datetime
import logging.handlers
import os.path
import sys
from json import JSONDecodeError
from pathlib import Path
from time import sleep

import urllib3.util
from bs4 import BeautifulSoup, PageElement, Tag
import pytermor as pt
import typing as t

import requests
import yt_dlp
from requests import Response


class ImExporter:
    PAGE_SIZE = 100
    URL = "https://vk.com/al_im.php"
    HOST = (u := urllib3.util.parse_url(URL)).scheme + '://' + u.host

    OUT_DIR = Path(__file__).parent / 'out'
    RENDERED_MSG_PAGE_LIMIT = 1000

    HTML_HEAD = f'<html>' \
                f'<head>' \
                f'<meta charset="utf-8">' \
                f'<link rel="stylesheet" type="text/css" href="default.css">' \
                f'</head>' \
                f'<body>'

    def __init__(self, peer: int):
        self._peer = peer

        cookiejar = yt_dlp.cookies.extract_cookies_from_browser("chrome", "Profile 1")
        self._cookies = {c.name: c.value for c in cookiejar.get_cookies_for_url(self.URL)}

        self._out_dir = self.OUT_DIR / str(peer)
        os.makedirs(self._out_dir, exist_ok=True)

        self._nums_rcvd = set()
        self._msg_ids_rcvd = set()
        self._index_file = open(self._out_dir / 'index.txt', 'wt')

        now_ts = datetime.now().timestamp()
        self._write_index('#', int(now_ts), f'INDEX FOR PEER {self._peer}', 0, 'ID')
        self._index_file.write('-' * 120 + '\n')

        self._out_rendered_msg_count = 0
        self._out_rendered: list[t.TextIO] = []

        self._photos_handler = PhotosHandler(self._out_dir)
        self._audiomsgs_handler = AudioMsgsHandler(self._out_dir)
        self._images_handler = ImagesHandler(self._out_dir)

    def run(self) -> None:
        index_count = 0
        rendered_count = 0

        try:
            print(f'Querying last page', end='         ')
            _, data = self._fetch_im_data()
            nums = [*(num for num, *_ in self._handle_response_data(data))]
            max_num = max(nums or [0])

        except RuntimeError as e:
            logging.exception(e)
            return

        max_num_len = len(str(max_num))
        fmt_num = lambda n='', fn=str.rjust: fn(str(n), max_num_len)

        if max_num:
            max_page = math.ceil(max_num // self.PAGE_SIZE)
        else:
            max_page = -1
        max_page_len = len(str(max_page))
        fmt_page = lambda p='': str(p).rjust(max_page_len)

        print(f'{fmt_num(max_num)} messages', end='')

        for page in range(max_page, -2, -1):
            # page -1 is the last one, without an offset

            offset = (page * self.PAGE_SIZE) + 30
            if offset < 0:
                offset = 0
            print(f'\nReq {fmt_page(max_page-page+1)}/{fmt_page(max_page+2)}'
                  f'  offset {fmt_num(offset)}…', end=' ')

            try:
                rendered, data = self._fetch_im_data(offset)
                soup = BeautifulSoup(rendered, features='html.parser')

                rendered_count_cur = self._filter_rendered_duplicates(soup)

                index_count_cur = 0
                for values in self._handle_response_data(data):
                    if self._write_index(*values):
                        index_count_cur += 1

                extra_count = rendered_count_cur - index_count_cur
                extra_str = fmt_num(['', f'+{extra_count}'][extra_count > 0], str.ljust)
                print(f'{fmt_num(index_count_cur)}{extra_str}', end='  ')

                self._images_handler.handle(soup)
                self._photos_handler.handle(soup)
                self._audiomsgs_handler.handle(soup)
                self._append_rendered(soup, offset, rendered_count_cur)

                index_count += index_count_cur
                rendered_count += rendered_count_cur

            except RuntimeError as e:
                print()
                logging.exception(e)
                continue

            sleep(0.05)

        shutil.copy(self.OUT_DIR / 'default.css', self._out_dir / 'default.css')
        self._finalize_out_rendered()
        print(f'\nDone, {fmt_num(index_count)} in index, {fmt_num(rendered_count)} rendered')

    def _fetch_im_data(self, offset: int = 0) -> tuple[str, dict]:
        response = requests.get(
            self.URL,
            params={
                "act": "a_history",
                "al": 1,
                "gid": 0,
                "im_v": 3,
                "offset": offset,
                "peer": self._peer,
                "toend": 0,
                "whole": 0,
            },
            cookies=self._cookies,
            headers={
                "authority": "vk.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9,bg;q=0.8,sr;q=0.7,ja;q=0.6,tg;q=0.5,zu;q=0.4,ru;q=0.3",
                "cache-control": "no-cache",
                "content-type": "application/x-www-form-urlencoded",
                "dnt": "1",
                "origin": "https://vk.com",
                "pragma": "no-cache",
                "referer": f"https://vk.com/im?sel={self._peer}",
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
        print(pt.format_si_binary(len(response.text), 'B').rjust(10), end='   ')

        try:
            rendered, data, *_ = response.json()['payload'][1]
            return rendered, data
        except KeyError:
            raise RuntimeError(f"Failed to read payload: {j:.1000s}")

    def _filter_rendered_duplicates(self, soup: BeautifulSoup) -> int:
        count = 0
        for li in soup.find_all('li', attrs={'class': 'im-mess'}):
            try:
                msg_id = int(li['data-msgid'])
            except ValueError as e:
                logging.warning(f"Message element without ID: {li}")
                continue

            if msg_id in self._msg_ids_rcvd:
                li.replace_with('')
                continue
            self._msg_ids_rcvd.add(msg_id)
            count += 1
        return count

    def _append_rendered(self, soup: BeautifulSoup, offset: int, rendered_count_cur: int) -> None:
        self._out_rendered_msg_count += rendered_count_cur
        if self._out_rendered_msg_count > self.RENDERED_MSG_PAGE_LIMIT or not len(self._out_rendered):
            self._out_rendered_msg_count = rendered_count_cur
            self._out_rendered.append(open(self._out_dir / f'rendered{len(self._out_rendered)+1}.html', 'wt'))
            self._out_rendered[-1].write(self.HTML_HEAD)

        marker = f'<!-- offset={offset} --> '
        self._out_rendered[-1].write(marker + str(soup) + '\n')

    def _finalize_out_rendered(self) -> None:
        for idx, out_ren in enumerate(self._out_rendered):
            self._out_rendered[idx].write('<br>')
            if idx > 0:
                self._out_rendered[idx].write(f'<a href="rendered{idx}.html">&lt;&lt; Prev&nbsp;</a>')
            if idx < len(self._out_rendered) - 1:
                self._out_rendered[idx].write(f'<a href="rendered{idx+2}.html">&nbsp;Next &gt;&gt;</a>')
            self._out_rendered[idx].write('</body></html>')

    def _handle_response_data(self, data: dict|t.Any) -> t.Iterable[tuple[int, int, str, int, int]]:
        if not data:
            return
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid response: {data!r}")
        for _, msg in data.items():
            msg_id, _1, _2, ts, text, attach, _6, _7, num, *_ = msg
            attach_count = int(attach.get('attach_count', 0))
            yield num, ts, text, attach_count, msg_id

    def _write_index(self, num: int|str, ts: int, text: str, attach_count: int, msg_id: int|str) -> bool:
        if isinstance(num, int):
            if num in self._nums_rcvd:
                return False
            self._nums_rcvd.add(num)

        numstr = ('('+str(num)+')').rjust(10)
        msg_id = str(msg_id).rjust(8)
        dt = datetime.fromtimestamp(ts)
        dtstr = dt.strftime("[%0e-%b-%y %H:%M:%S]")
        attachstr = ['', f'[+{attach_count:d}A] '][attach_count > 0]
        self._index_file.write(' '.join([numstr, msg_id, '|', str(ts), dtstr, '|', attachstr+text]) + '\n')
        self._index_file.flush()
        return True

    def close(self) -> None:
        for out_ren in self._out_rendered:
            out_ren.close()


class Downloader(metaclass=ABCMeta):
    COUNTER_COMPACT_THRESHOLD = 10

    def __init__(self, out_dir: Path):
        self._out_dir = out_dir
        os.makedirs(self._get_out_subdir(), exist_ok=True)

        self._counter_max = 0

    @abstractmethod
    def _get_type(self) -> str:
        ...

    @abstractmethod
    def _get_out_subdir(self) -> Path:
        ...

    def _init_counter(self, counter_max: int) -> None:
        self._counter_max = counter_max
        self._update_counter(None)

    @property
    def _is_compact_counter(self) -> bool:
        return self._counter_max >= self.COUNTER_COMPACT_THRESHOLD

    def _preupdate_counter(self) -> None:
        if self._is_compact_counter:
            return
        print('*', end='')

    def _update_counter(self, counter: int|None) -> None:
        letter = self._get_type()[0].upper()

        if self._is_compact_counter:
            width = len(str(self._counter_max))
            if counter is not None:
                print('\b'*(width + 3), end='')
            print(f'{counter or 0:>{width}d}×{letter} ', end='')
            return

        if counter is None:
            return

        print('\b'+letter, end='')

    def _download(self, url: str) -> Path:
        remote_path = urllib3.util.parse_url(url).path
        basename = os.path.basename(remote_path)
        if len(basename) < 10:
            basename = re.sub(r'[^\d\w]+', '-', remote_path).strip('-')

        local_abs_path = self._get_out_subdir() / basename
        if local_abs_path.exists():
            return local_abs_path

        try:
            response = requests.get(url)
        except requests.exceptions.MissingSchema:
            response = requests.get(ImExporter.HOST + url)

        if not response.ok:
            raise RuntimeError(f"Failed to download {self._get_type()} (HTTP {response.status_code}): {url}")

        with open(local_abs_path, 'wb') as f:
            f.write(response.content)
        return local_abs_path


class PhotosHandler(Downloader):
    def handle(self, soup: BeautifulSoup) -> None:
        a_list = soup.find_all(name='a', attrs={'aria-label': 'фотография'})
        if not len(a_list):
            return
        self._init_counter(len(a_list))

        for idx, a in enumerate(a_list):
            try:
                self._preupdate_counter()

                source_url = self._extract_from_onclick(a['onclick'])
                source_local_abs_path = self._download(source_url)
                source_local_rel_path = source_local_abs_path.relative_to(self._out_dir)

                thumb_url = self._extract_from_style(a['style'])
                thumb_local_abs_path = self._download(thumb_url, thumb=True)
                thumb_local_rel_path = thumb_local_abs_path.relative_to(self._out_dir)

                a['href'] ='./'+str(source_local_rel_path)
                a['style'] = a['style'].replace(thumb_url, './'+str(thumb_local_rel_path))
                a['target'] = '_blank'
                del a['onclick']

            except ValueError as e:
                raise RuntimeError(f"Failed to handle photo: {a}") from e

            else:
                self._update_counter(idx)

    def _extract_from_onclick(self, onclick: str) -> str:
        jmatch = re.search(r'(?:showPhoto|showManyPhoto.pbind)\(.+?(\{.+\}).*\)', onclick)
        if not jmatch:
            raise ValueError(f"Data JSON not found for photo")
        try:
            j = json.loads(jmatch.group(1))
        except JSONDecodeError as e:
            raise ValueError(f"Invalid data JSON for photo: {onclick!r}") from e

        maxsize, maxurl = 0, None
        for sizename, urldef in j['temp'].items():
            if not isinstance(urldef, list) or not len(urldef) == 3:
                continue
            if (size := operator.mul(*map(int, urldef[1:3]))) > maxsize:
                maxsize = size
                maxurl = urldef[0]
        return maxurl

    def _extract_from_style(self, style: str) -> str:
        urlmatch = re.search(r'background-image:\s+url\((.+)\);', style)
        if not urlmatch:
            raise ValueError(f"Thumb URL not found for photo")
        return urlmatch.group(1)

    def _get_out_subdir(self) -> Path:
        return self._out_dir / 'photo'

    def _get_type(self) -> str:
        return 'photo'

    def _download(self, url: str, thumb=False) -> Path:
        remote_path = urllib3.util.parse_url(url).path
        basename = os.path.basename(remote_path)
        if thumb:
            name, ext = os.path.splitext(basename)
            basename = f'{name}_{ext}'

        local_abs_path = Path(self._get_out_subdir()) / basename
        if local_abs_path.exists():
            return local_abs_path

        response = requests.get(url)
        if not response.ok:
            raise RuntimeError(f"Failed to download photo (HTTP {response.status_code}): {url}")

        with open(local_abs_path, 'wb') as f:
            f.write(response.content)
        return local_abs_path


class AudioMsgsHandler(Downloader):
    def handle(self, soup: BeautifulSoup) -> None:
        div_list = soup.find_all(name='div', attrs={'class': "audio-msg-track"})
        if not len(div_list):
            return
        self._init_counter(len(div_list))

        for idx, div in enumerate(div_list):
            self._preupdate_counter()

            try:
                try:
                    data_local_abs_path = self._download(data_url := div['data-mp3'])
                except RuntimeError as e:
                    data_local_abs_path = self._download(data_url := div['data-ogg'])
                data_local_rel_path = data_local_abs_path.relative_to(self._out_dir)

                a = soup.new_tag('a', attrs=dict(href=data_url, target='_blank'))
                a.append(data_url)
                div.append(a)

            except ValueError as e:
                raise RuntimeError(f"Failed to handle audio message: {a}") from e

            else:
                self._update_counter(idx)

    def _get_out_subdir(self) -> Path:
        return self._out_dir / 'audiomsg'

    def _get_type(self) -> str:
        return 'audiomsg'


class ImagesHandler(Downloader):
    def handle(self, soup: BeautifulSoup) -> None:
        img_list = soup.find_all(name='img')
        if not len(img_list):
            return
        self._init_counter(len(img_list))

        for idx, img in enumerate(img_list):
            self._preupdate_counter()

            try:
                local_abs_path = self._download(img['src'])
                local_rel_path = local_abs_path.relative_to(self._out_dir)
                img['src'] ='./'+str(local_rel_path)

            except ValueError as e:
                raise RuntimeError(f"Failed to handle photo: {img}") from e

            else:
                self._update_counter(idx)

    def _get_out_subdir(self) -> Path:
        return self._out_dir / 'image'

    def _get_type(self) -> str:
        return 'image'


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f'USAGE:\n    {sys.argv[0]} PEER_ID')
        exit(1)

    try:
        peer_id = int(sys.argv[1])
    except ValueError:
        print("PEER_ID should be an integer")
        exit(1)

    exp = ImExporter(peer=peer_id)
    exp.run()
    exp.close()

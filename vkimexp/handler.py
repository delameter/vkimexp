# ------------------------------------------------------------------------------
#  vkimexp [VK dialogs exporter]
#  (c) 2023 A. Shavykin <0.delameter@gmail.com>
# ------------------------------------------------------------------------------

import json
import operator
import os.path
import re
from abc import abstractmethod, ABCMeta
from json import JSONDecodeError
from pathlib import Path

import requests
from bs4 import BeautifulSoup, ResultSet
from urllib3.util import parse_url

from .common import Context, HOST, AttachmentEventTypeEnum
from .common import DownloadError


class AttachmentHandler(metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    def get_type(cls) -> str:
        ...

    def __init__(self, ctx: Context):
        self._ctx = ctx
        os.makedirs(self._get_out_subdir(), exist_ok=True)

        self.url_to_abs_path_map: dict[str, Path] = dict()
        self.prepared: ResultSet | None = None

    @abstractmethod
    def prepare(self, soup: BeautifulSoup) -> int:
        ...

    @abstractmethod
    def handle(self, soup: BeautifulSoup, attachment_event_cb: callable) -> None:
        ...

    def _get_out_subdir(self) -> Path:
        return self._ctx.out_dir / self.get_type()

    def _download(self, url: str) -> Path:
        remote_path = parse_url(url).path
        basename = os.path.basename(remote_path)
        if len(basename) < 10:
            basename = re.sub(r'[^\d\w]+', '-', remote_path).strip('-')

        local_abs_path = self._get_out_subdir() / basename
        if local_abs_path.exists():
            return local_abs_path

        self._ctx.totals.attach_found.increment()
        try:
            response = requests.get(url)
        except requests.exceptions.MissingSchema:
            response = requests.get(HOST + url)

        if not response.ok:
            raise DownloadError(f"Failed to download {self.get_type()} (HTTP {response.status_code}): {url}")

        with open(local_abs_path, 'wb') as f:
            f.write(response.content)

        self._ctx.totals.attach_downloaded.increment()
        return local_abs_path


class PhotosHandler(AttachmentHandler):
    @classmethod
    def get_type(cls) -> str:
        return 'photo'

    def prepare(self, soup: BeautifulSoup) -> int:
        self.prepared = soup.find_all(name='a', attrs={'aria-label': 'фотография'})
        return len(self.prepared)

    def handle(self, soup: BeautifulSoup, attachment_event_cb: callable) -> None:
        for idx, a in enumerate(self.prepared):
            try:
                source_url = self._extract_from_onclick(a['onclick'])
                if source_url in self.url_to_abs_path_map.keys():
                    source_local_abs_path = self.url_to_abs_path_map[source_url]
                else:
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.STARTED, source_url)
                    source_local_abs_path = self._download(source_url)
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.SUCCESS, source_local_abs_path)
                    self.url_to_abs_path_map[source_url] = source_local_abs_path
                source_local_rel_path = source_local_abs_path.relative_to(self._ctx.out_dir)
            except (DownloadError, ValueError) as e:
                attachment_event_cb(self, idx, AttachmentEventTypeEnum.FAILED, e)
                continue

            try:
                thumb_url = self._extract_from_style(a['style'])
                if thumb_url in self.url_to_abs_path_map.keys():
                    thumb_local_abs_path = self.url_to_abs_path_map[thumb_url]
                else:
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.PARTIAL, thumb_url)
                    thumb_local_abs_path = self._download(thumb_url, thumb=True)
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.SUCCESS, thumb_local_abs_path)
                    self.url_to_abs_path_map[thumb_url] = thumb_local_abs_path
                thumb_local_rel_path = thumb_local_abs_path.relative_to(self._ctx.out_dir)
            except (DownloadError, ValueError) as e:
                attachment_event_cb(self, idx, AttachmentEventTypeEnum.FAILED, e)
                continue

            a['href'] = './' + str(source_local_rel_path)
            a['style'] =  'display: block; background-size: contain; ' + a['style'].replace(thumb_url, './' + str(thumb_local_rel_path))
            a['target'] = '_blank'
            del a['onclick']

    @classmethod
    def _extract_from_onclick(cls, onclick: str) -> str:
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

    @classmethod
    def _extract_from_style(cls, style: str) -> str:
        urlmatch = re.search(r'background-image:\s+url\((.+)\);', style)
        if not urlmatch:
            raise ValueError(f"Thumb URL not found for photo")
        return urlmatch.group(1)

    def _download(self, url: str, thumb=False) -> Path:
        remote_path = parse_url(url).path
        basename = os.path.basename(remote_path)
        if thumb:
            name, ext = os.path.splitext(basename)
            basename = f'{name}_{ext}'

        local_abs_path = Path(self._get_out_subdir()) / basename
        if local_abs_path.exists():
            return local_abs_path

        self._ctx.totals.attach_found.increment()
        response = requests.get(url)
        if not response.ok:
            raise DownloadError(f"Failed to download photo (HTTP {response.status_code}): {url}")

        with open(local_abs_path, 'wb') as f:
            f.write(response.content)

        self._ctx.totals.attach_downloaded.increment()
        return local_abs_path


class AudioMsgsHandler(AttachmentHandler):
    @classmethod
    def get_type(self) -> str:
        return 'audiomsg'

    def prepare(self, soup: BeautifulSoup) -> int:
        self.prepared = soup.find_all(name='div', attrs={'class': "audio-msg-track"})
        return len(self.prepared)

    def handle(self, soup: BeautifulSoup, attachment_event_cb: callable) -> None:
        for idx, div in enumerate(self.prepared):
            data_urls = [div.get('data-mp3'), div.get('data-ogg')]
            last_error = None
            local_abs_path = None

            while local_abs_path is None:
                if not len(data_urls):
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.FAILED, last_error)
                    break

                url = data_urls.pop(0)
                if url in self.url_to_abs_path_map.keys():
                    local_abs_path = self.url_to_abs_path_map[url]
                else:
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.STARTED, url)
                    try:
                        local_abs_path = self._download(url)
                    except DownloadError as e:
                        last_error = e
                        continue
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.SUCCESS, local_abs_path)
                    self.url_to_abs_path_map[url] = local_abs_path

                local_rel_path = local_abs_path.relative_to(self._ctx.out_dir)
                a = soup.new_tag('a', attrs=dict(href=local_rel_path, target='_blank'))
                a.append(str(local_rel_path))
                div.append(a)


class ImagesHandler(AttachmentHandler):
    @classmethod
    def get_type(cls) -> str:
        return 'image'

    def prepare(self, soup: BeautifulSoup) -> int:
        self.prepared = soup.find_all(name='img')
        return len(self.prepared)

    def handle(self, soup: BeautifulSoup, attachment_event_cb: callable) -> None:
        for idx, img in enumerate(self.prepared):
            url = img['src']
            try:
                if url in self.url_to_abs_path_map.keys():
                    local_abs_path = self.url_to_abs_path_map[url]
                else:
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.STARTED, url)
                    local_abs_path = self._download(url)
                    attachment_event_cb(self, idx, AttachmentEventTypeEnum.SUCCESS, local_abs_path)
                    self.url_to_abs_path_map[url] = local_abs_path

                local_rel_path = local_abs_path.relative_to(self._ctx.out_dir)
                img['src'] = './' + str(local_rel_path)
            except DownloadError as e:
                attachment_event_cb(self, idx, AttachmentEventTypeEnum.FAILED, e)

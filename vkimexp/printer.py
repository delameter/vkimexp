# ------------------------------------------------------------------------------
#  vkimexp [VK dialogs exporter]
#  (c) 2023 A. Shavykin <0.delameter@gmail.com>
# ------------------------------------------------------------------------------

import enum
import re
import sys
import typing as t
from functools import cached_property
from time import sleep

import pytermor as pt
from pytermor import Styles as BaseStyles

from .common import Context, PAGE_SIZE, AttachmentEventTypeEnum


class Styles(BaseStyles):
    def __init__(self):
        self.REQUEST_FAILED = BaseStyles.ERROR_LABEL
        self.REQUEST_SUCCESS = pt.FrozenStyle(fg=pt.cv.GREEN, bold=True)


class ColumnEnum(enum.IntEnum):
    STATUS = 0
    REQ_NUM = enum.auto()
    PROGRESS = enum.auto()
    OFFSET = enum.auto()
    SIZE = enum.auto()
    MSG_COUNT = enum.auto()
    ATTACHMENTS = enum.auto()


class StatePrinter:
    SEP_SIZE = 2

    def __init__(self, ctx: Context, io_: t.TextIO = None):
        self._ctx = ctx
        self._io = io_ or sys.stdout
        self._styles = Styles()
        self._size_formatter = pt.StaticFormatter(pt.formatter_bytes_human, auto_color=True, unit='b')

        self._cur_column_idx = 0
        self._column_widths = []
        self._cur_attach_states = ""
        self._cur_attach_idx = None

        self._print(f'Estimating…')

    def _print(self, val: pt.RT = "", nl=False):
        if isinstance(val, pt.IRenderable):
            val = val.render()
        pt.echo(val, file=self._io, nl=nl, flush=True)

    def _print_cell(self, val: pt.RT, idx: int = None, align: str = "<"):
        if idx is not None:
            self._cur_column_idx = idx
            self._move_cursor_to_column()

        width = self._get_column_width() - self.SEP_SIZE
        val = pt.Text(val, width=width, align=align)
        sep = pt.pad(self.SEP_SIZE)
        self._print(val.render() + sep)

        if self._cur_column_idx < len(self._column_widths) - 1:
            # if not last column, move to next
            # transition "last column -> column 0 (next row)" is performed manually
            self._cur_column_idx += 1

    def _next_row(self):
        self._print(nl=True)
        self._cur_column_idx = 0

    def print_init_attempt(self, attempts: int):
        self._print('#')

    def print_header(self):
        t = f'  {self._req_total} queries, ' + pt.highlight(str(self._ctx.max_msg_idx)) + ' messages'
        self._print(t)
        self._next_row()
        self._print_sep()

    def print_pre_request(self):
        self._cur_attach_states = ""
        self._print_cell('·')

        req_cur = self._ctx.max_page - self._ctx.page + 1
        req_cur_str = str(req_cur).rjust(self._max_page_len)
        self._print_cell(f'{req_cur_str}/{self._req_total}')

        req_ratio_str = pt.format_auto_float(100*req_cur/self._req_total, 4)
        self._print_cell(f'{req_ratio_str}%')

        offset_str = 'offset ' + pt.highlight(str(self._ctx.offset).rjust(self._max_idx_len)) + '…'
        self._print_cell(offset_str)

    def print_post_request(self, size: int, msg_num: int, msg_extra_num: int):
        self._print_cell(self._size_formatter.format(size), align='>')

        msg_str = str(msg_num).rjust(self._max_idx_len)
        if msg_extra_num > 0:
            msg_str += f'+{msg_extra_num}'.ljust(self._max_idx_len)
        else:
            msg_str += pt.pad(self._max_idx_len)
        self._print_cell(msg_str, align="<")

    def print_attachment(self, type_letter: str, attach_idx: str, event_type: AttachmentEventTypeEnum):
        char = '·'
        match event_type:
            case AttachmentEventTypeEnum.PARTIAL:
                char = '*'
            case AttachmentEventTypeEnum.FAILED:
                char = 'E'
            case AttachmentEventTypeEnum.SUCCESS:
                char = type_letter
        if attach_idx == self._cur_attach_idx:
            self._cur_attach_states = self._cur_attach_states[:-1]
        self._cur_attach_states += char
        self._cur_attach_idx = attach_idx

        packed_states = re.sub(R'((.)\2{4,})', lambda m: '('+str(len(m.group(1)))+')'+m.group(2), self._cur_attach_states)
        frags = []
        for part in re.split(R'(E+)', packed_states):
            if set(part) == {'E'}:
                frags.append(pt.Fragment(part, self._styles.REQUEST_FAILED))
            else:
                frags.append(part)
        self._print_cell(pt.Composite(*frags), ColumnEnum.ATTACHMENTS)

    def print_failed_request(self, e: Exception):
        self._print_cell(pt.Fragment('E', self._styles.REQUEST_FAILED), ColumnEnum.STATUS)
        self._next_row()

    def print_completed_request(self):
        self._print_cell(pt.Fragment('S', self._styles.REQUEST_SUCCESS), ColumnEnum.STATUS)
        self._next_row()

    def _get_column_width(self, idx: int = None) -> int:
        if idx is None:
            idx = self._cur_column_idx
        if not self._column_widths:
            self._compute_column_widths()
        return self._column_widths[idx]

    def _compute_column_widths(self):
        self._column_widths.clear()
        max_width = self._max_width
        fixed_widths = [
            1,
            self._max_page_len * 2 + 1,
            5,
            6 + 1 + self._max_idx_len + 1,
            6,
            self._max_msg_count_len * 2 + 1,
        ]
        while len(fixed_widths) and (sum(self._column_widths) + fixed_widths[0] + self.SEP_SIZE <= max_width):
            self._column_widths.append(fixed_widths.pop(0) + self.SEP_SIZE)

        if len(fixed_widths):
            return
        self._column_widths.append(max(0, max_width - sum(self._column_widths) - self.SEP_SIZE))

    def _move_cursor_to_column(self):
        prev_column_widths = sum(self._get_column_width(c) for c in range(self._cur_column_idx))
        self._print(pt.make_set_cursor_column(prev_column_widths+1).assemble())

    @property
    def _req_total(self) -> int:
        return self._ctx.max_page + 2

    @property
    def _max_width(self) -> int:
        return min(80, pt.get_terminal_width())

    @cached_property
    def _max_idx_len(self):
        return len(str(self._ctx.max_msg_idx))

    @cached_property
    def _max_page_len(self):
        return len(str(self._ctx.max_page))

    @cached_property
    def _max_msg_count_len(self):
        return len(str(PAGE_SIZE))

    def _print_sep(self):
        self._print('-' * self._max_width)
        self._next_row()

    def print_footer(self):
        self._print_sep()

        tot = self._ctx.totals
        self._print(f'   Messages (indexed/rendered):  {tot.msg_count_index.value}/{tot.msg_count_html.value}', nl=True)
        self._print(f'Attachments (found/downloaded):  {tot.attach_found.value}/{tot.attach_downloaded.value}', nl=True)
        self._print(f'              Output directory:  {self._ctx.out_dir!s}', nl=True)

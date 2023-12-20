import logging.handlers

import click
from yt_dlp import SUPPORTED_BROWSERS

from .common import init_logging, get_logger
from .core import Task


@click.command(no_args_is_help=True)
@click.argument("peers", nargs=-1, required=True, type=click.STRING)
@click.option("-b", "--browser", metavar="NAME", type=click.Choice(SUPPORTED_BROWSERS), default='chrome',
              show_default=True, help="Browser to use cookies from (process is automatic).")
@click.option("-v", "--verbose", count=True, type=click.IntRange(0, 1, clamp=True))
@click.pass_context
def entrypoint(clctx: click.Context, peers: list[str], verbose: int, **kwargs):
    """
    PEER should be VK ID of a person or a conversation in question (several
    PEERs can be provided at once). To find PEER of a person, open this page:
    https://vk.com/im and select the required dialog, and then his/her VK ID
    will appear in the address bar like this:

        https://vk.com/im?sel=1234567890

    where 1234567890 is a numeric ID in question. Use this number as PEER, e.g.
    for a person with VK ID 1234567890 the command is:

        vkimexp 1234567890

    For group conversations there is no VK ID in the URL, as they are identified
    differently, by their index. Nevertheless, take this number (together with 'c'!)
    and provide it as is, the application will figure out VK ID of a conversation
    by itself:

        https://vk.com/im?sel=c195  =>  vkimexp c195

    """
    peer_ids = [_normalize_peer_id(p) for p in peers]
    init_logging(verbose)

    for peer_id in peer_ids:
        task = Task(clctx, peer_id)
        try:
            task.run()
        except Exception as e:
            if verbose:
                get_logger().exception(e)
            else:
                get_logger().error(e)
        finally:
            task.close()


def _normalize_peer_id(peer: str) -> int:
    try:
        if peer.startswith('c'):
            return 2000000000 + int(peer[1:])
        peer = int(peer)
        assert peer > 0, f"PEER should be > 0, got: {peer}"
        return peer
    except ValueError:
        raise RuntimeError(f"Invalid PEER format: {peer}, run 'vkimexp --help' for the details")

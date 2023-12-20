#!/bin/sh
#-------------------------------------------------------------------------------
# vkimexp [VK dialogs exporter]
# (c) 2023 A. Shavykin <0.delameter@gmail.com>
#-------------------------------------------------------------------------------

export XDG_CURRENT_DESKTOP=GNOME
export PYTHONPATH=.

./venv/bin/python -m vkimexp "$@"

#!/bin/sh

export XDG_CURRENT_DESKTOP=GNOME
export PYTHONPATH=.

./venv/bin/python main.py "$@"

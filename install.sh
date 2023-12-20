#!/bin/sh

python3.12 -m venv venv --clear
./venv/bin/pip install -r requirements.txt

mv requirements.txt requirements.txt.old
./venv/bin/pip freeze --exclude-editable -r requirements.txt.old |
  sed -nEe '/==/p' |
  sort > requirements.txt

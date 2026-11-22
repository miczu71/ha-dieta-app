#!/bin/sh
# Ustawienia czyta sam add-on z /data/options.json (dieta/settings.py); TZ ustawia Supervisor.
set -e
umask 077
exec python3 -m dieta

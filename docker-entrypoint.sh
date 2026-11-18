#!/bin/sh
set -eu

mkdir -p /app/data/media
chown factory:factory /app/data /app/data/media
exec gosu factory "$@"

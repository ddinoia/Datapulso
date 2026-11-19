#!/usr/bin/env bash
# Entorno de desarrollo local sin Docker: levanta PostgreSQL 16 (puerto 55432) y MariaDB/MySQL (puerto 53306).
# Uso: scripts/dev_env.sh start|stop|status
# Para Docker usar docker-compose.yml (sin probar en el hito 1, ver docs/REVISION_HITO1.md).
set -euo pipefail
PGDIR=${DP_PGDIR:-/tmp/dp_pg}
MYDIR=${DP_MYDIR:-/tmp/dp_my}
PGPORT=${DP_PGPORT:-55432}
MYPORT=${DP_MYPORT:-53306}
PGBIN=/usr/lib/postgresql/16/bin

start_pg() {
  if [ ! -d "$PGDIR/data" ]; then
    mkdir -p "$PGDIR" && chown postgres:postgres "$PGDIR"
    runuser -u postgres -- "$PGBIN/initdb" -D "$PGDIR/data" -U postgres --auth=trust -E UTF8 --locale=C.UTF-8 >/dev/null
  fi
  if ! runuser -u postgres -- "$PGBIN/pg_isready" -q -p "$PGPORT" -h 127.0.0.1; then
    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDIR/data" -o "-p $PGPORT -k $PGDIR -c listen_addresses=127.0.0.1" -l "$PGDIR/pg.log" -w start >/dev/null
  fi
  echo "postgres listo en 127.0.0.1:$PGPORT (usuario postgres, sin contraseña, solo desarrollo)"
}

start_my() {
  if [ ! -d "$MYDIR/data" ]; then
    mkdir -p "$MYDIR" && chown mysql:mysql "$MYDIR"
    runuser -u mysql -- mariadb-install-db --datadir="$MYDIR/data" --auth-root-authentication-method=normal >/dev/null 2>&1
  fi
  if ! mariadb-admin -h 127.0.0.1 -P "$MYPORT" -uroot ping >/dev/null 2>&1; then
    (runuser -u mysql -- mariadbd --datadir="$MYDIR/data" --port="$MYPORT" --bind-address=127.0.0.1 \
      --socket="$MYDIR/my.sock" --pid-file="$MYDIR/my.pid" >"$MYDIR/my.log" 2>&1 &)
    for _ in $(seq 1 40); do
      mariadb-admin -h 127.0.0.1 -P "$MYPORT" -uroot ping >/dev/null 2>&1 && break; sleep 0.5
    done
  fi
  echo "mysql(mariadb) listo en 127.0.0.1:$MYPORT (usuario root sin contraseña, solo desarrollo)"
}

case "${1:-start}" in
  start) start_pg; start_my ;;
  stop)
    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDIR/data" -m fast stop || true
    mariadb-admin -h 127.0.0.1 -P "$MYPORT" -uroot shutdown || true ;;
  status)
    runuser -u postgres -- "$PGBIN/pg_isready" -p "$PGPORT" -h 127.0.0.1 || true
    mariadb-admin -h 127.0.0.1 -P "$MYPORT" -uroot ping || true ;;
esac

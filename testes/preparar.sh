#!/bin/sh
# Sobe o banco da fachada e o ambiente Python na máquina de testes.
set -e
RAIZ=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BASE=/opt/panthera-track
mkdir -p "$BASE"
ENV="$BASE/.env"
if [ ! -f "$ENV" ]; then
  umask 077
  [ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
  "$BASE/venv/bin/pip" install -q cryptography
  CHAVE=$("$BASE/venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  DONO=$(openssl rand -hex 24)
  API=$(openssl rand -hex 24)
  cat > "$ENV" <<FIM
TRACK_SENHA_DONO=$DONO
TRACK_SENHA_API=$API
TRACK_CHAVE=$CHAVE
TRACK_BANCO_DSN=postgresql://track_api:$API@127.0.0.1:5434/track
TRACK_BANCO_DONO_DSN=postgresql://track_dono:$DONO@127.0.0.1:5434/track
FIM
fi
[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q -r "$RAIZ/fachada/requirements.txt"
. "$ENV"
docker compose --env-file "$ENV" -f "$RAIZ/infra/banco/docker-compose.yml" --project-name track-banco up -d --wait
docker exec -i track-banco psql -q -U track_dono -d track -v ON_ERROR_STOP=1 <<FIM
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'track_api') THEN
    CREATE ROLE track_api LOGIN PASSWORD '$TRACK_SENHA_API';
  END IF;
END \$\$;
FIM
JA=$(docker exec track-banco psql -tA -U track_dono -d track -c "SELECT to_regclass('clientes') IS NOT NULL")
if [ "$JA" != "t" ]; then
  docker exec -i track-banco psql -q -U track_dono -d track -v ON_ERROR_STOP=1 < "$RAIZ/banco/migracoes/001_base.sql"
fi
echo pronto

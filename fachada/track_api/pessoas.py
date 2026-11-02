"""Administração de pessoas: cria a conta, os vínculos e o usuário-sombra em cada instância.

Roda no servidor com o papel dono e acesso ao Docker, que a API não tem.
python -m track_api.pessoas criar --nome "Ana" --email ana@exemplo.org --tipo analista --clientes alfa,beta --saida /root/ana.json
python -m track_api.pessoas criar --nome "Bia" --email bia@exemplo.org --tipo cliente --clientes alfa --papel gestor --saida /root/bia.json
"""
import argparse
import json
import os
import secrets
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .banco import cifrar
from .motor import PERFIL_CLIENTE, PERFIL_FACHADA
from .provisao import CLIENTES, _console, _rodar_php
from .sessao import hasher


def _criar_sombra(conn, pessoa_id, cliente, perfil, nome, email):
    identificador = cliente["identificador"]
    pasta, projeto = CLIENTES / identificador, f"track-{identificador}"
    usuario, senha = f"p{secrets.token_hex(8)}", secrets.token_hex(24)
    _console(pasta, projeto, "user:create", usuario, "-p", senha)
    _console(pasta, projeto, "user:grant", usuario, "-p", perfil, "-e", "0", "-r")
    saida = _rodar_php(pasta, projeto, "definir_contato.php", {"usuario": usuario, "nome": nome, "email": email})
    conn.execute("INSERT INTO sombras (pessoa_id, cliente_id, motor_usuario, motor_senha, motor_id) VALUES (%s, %s, %s, %s, %s)",
                 (pessoa_id, cliente["id"], usuario, cifrar(senha), json.loads(saida.strip().splitlines()[-1])["id"]))


def criar(nome, email, tipo, clientes, papel=None):
    """Devolve o id e a senha provisória, que precisa ser trocada na primeira entrada."""
    if tipo == "cliente" and (len(clientes) != 1 or papel not in ("solicitante", "gestor")):
        raise ValueError("pessoa do cliente tem um cliente e um papel")
    if tipo == "analista" and papel:
        raise ValueError("analista não tem papel de cliente")
    senha = secrets.token_urlsafe(18)
    with psycopg.connect(os.environ["TRACK_BANCO_DONO_DSN"], row_factory=dict_row) as conn:
        linhas = [conn.execute("SELECT * FROM clientes WHERE nome = %s", (n,)).fetchone() for n in clientes]
        if not all(linhas):
            raise ValueError("cliente não encontrado")
        pessoa_id = conn.execute(
            "INSERT INTO pessoas (email, nome, tipo, cliente_id, papel, senha_hash) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (email.strip().lower(), nome, tipo, linhas[0]["id"] if tipo == "cliente" else None, papel, hasher.hash(senha))).fetchone()["id"]
        for cliente in linhas:
            if tipo == "analista":
                conn.execute("INSERT INTO vinculos (pessoa_id, cliente_id) VALUES (%s, %s)", (pessoa_id, cliente["id"]))
            _criar_sombra(conn, pessoa_id, cliente, PERFIL_FACHADA if tipo == "analista" else PERFIL_CLIENTE, nome, email.strip().lower())
    return {"pessoa_id": str(pessoa_id), "email": email.strip().lower(), "senha_provisoria": senha}


def desativar(email):
    with psycopg.connect(os.environ["TRACK_BANCO_DONO_DSN"]) as conn:
        conn.execute("UPDATE pessoas SET ativo = false WHERE email = %s", (email.strip().lower(),))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("criar")
    c.add_argument("--nome", required=True)
    c.add_argument("--email", required=True)
    c.add_argument("--tipo", required=True, choices=("analista", "cliente"))
    c.add_argument("--clientes", required=True, help="nomes separados por vírgula")
    c.add_argument("--papel", choices=("solicitante", "gestor"))
    c.add_argument("--saida", required=True, help="arquivo local que recebe a senha provisória, com permissão 600")
    d = sub.add_parser("desativar")
    d.add_argument("--email", required=True)
    a = p.parse_args()
    if a.comando == "desativar":
        desativar(a.email)
        return
    r = criar(a.nome, a.email, a.tipo, a.clientes.split(","), a.papel)
    saida = Path(a.saida)
    saida.touch(mode=0o600)
    saida.write_text(json.dumps(r, indent=2))
    print(f"pessoa {r['pessoa_id']} criada; senha provisória em {saida}")


if __name__ == "__main__":
    main()

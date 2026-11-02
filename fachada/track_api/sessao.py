"""Entrada das pessoas: senha, troca da senha provisória e segundo fator obrigatório."""
import base64
import hashlib
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import banco, mfa

COOKIE = "track_sessao"
_DURACAO_HORAS = 12
_TENTATIVAS = 5
_MINUTOS_DE_BLOQUEIO = 15

hasher = PasswordHasher()
# Conferir um hash mesmo sem conta: o tempo de resposta não pode dizer quais e-mails existem.
_HASH_FALSO = hasher.hash(secrets.token_hex(16))


def checar_origem(request: Request):
    """O cookie é SameSite=Strict; o cabeçalho próprio é a segunda barreira contra pedido forjado."""
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Track-Tela") != "1":
        raise HTTPException(403, "Pedido recusado")


router = APIRouter(prefix="/api/v1/sessao", dependencies=[Depends(checar_origem)])


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def etapa(sessao):
    if sessao["trocar_senha"]:
        return "trocar_senha"
    if not sessao["mfa_ativo"]:
        return "cadastrar_mfa"
    if not sessao["mfa_ok"]:
        return "codigo_mfa"
    return "pronto"


def sessao_atual(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Entre para continuar")
    with banco.transacao() as conn:
        linha = conn.execute(
            """SELECT s.token_hash, s.mfa_ok, p.* FROM sessoes s JOIN pessoas p ON p.id = s.pessoa_id
               WHERE s.token_hash = %s AND s.expira_em > now() AND p.ativo""", (_hash_token(token),)).fetchone()
    if not linha:
        raise HTTPException(401, "Entre para continuar")
    return linha


def pessoa_pronta(sessao=Depends(sessao_atual)):
    if etapa(sessao) != "pronto":
        raise HTTPException(403, "Conclua a entrada")
    return sessao


def _falha(conn, pessoa_id):
    conn.execute(
        """UPDATE pessoas SET
             bloqueado_ate = CASE WHEN falhas + 1 >= %s THEN now() + make_interval(mins => %s) ELSE bloqueado_ate END,
             falhas = CASE WHEN falhas + 1 >= %s THEN 0 ELSE falhas + 1 END
           WHERE id = %s""", (_TENTATIVAS, _MINUTOS_DE_BLOQUEIO, _TENTATIVAS, pessoa_id))


def _bloqueada(conn, pessoa_id):
    return conn.execute("SELECT bloqueado_ate > now() AS sim FROM pessoas WHERE id = %s", (pessoa_id,)).fetchone()["sim"]


_MUITAS = HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")


class Entrada(BaseModel):
    email: str = Field(max_length=320)
    senha: str = Field(max_length=1024)


class TrocaDeSenha(BaseModel):
    senha_atual: str = Field(max_length=1024)
    senha_nova: str = Field(min_length=12, max_length=1024)


class Codigo(BaseModel):
    codigo: str = Field(max_length=32)


def _apresentar(sessao):
    return {"etapa": etapa(sessao), "nome": sessao["nome"], "email": sessao["email"], "tipo": sessao["tipo"], "papel": sessao["papel"]}


@router.post("")
def entrar(dados: Entrada, resposta: Response):
    email = dados.email.strip().lower()
    with banco.transacao() as conn:
        pessoa = conn.execute("SELECT * FROM pessoas WHERE email = %s AND ativo", (email,)).fetchone()
        if pessoa and pessoa["bloqueado_ate"] and _bloqueada(conn, pessoa["id"]):
            raise _MUITAS
        try:
            hasher.verify(pessoa["senha_hash"] if pessoa else _HASH_FALSO, dados.senha)
            certo = pessoa is not None
        except (VerificationError, InvalidHashError):
            certo = False
        if pessoa and not certo:
            _falha(conn, pessoa["id"])
    if not certo:
        raise HTTPException(401, "E-mail ou senha incorretos")
    token = secrets.token_urlsafe(32)
    with banco.transacao() as conn:
        conn.execute("UPDATE pessoas SET falhas = 0, bloqueado_ate = NULL WHERE id = %s", (pessoa["id"],))
        conn.execute("DELETE FROM sessoes WHERE expira_em < now()")
        conn.execute("INSERT INTO sessoes (token_hash, pessoa_id, expira_em) VALUES (%s, %s, now() + make_interval(hours => %s))",
                     (_hash_token(token), pessoa["id"], _DURACAO_HORAS))
    resposta.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/", max_age=_DURACAO_HORAS * 3600,
                        secure=os.getenv("TRACK_COOKIE_SEGURO", "1") == "1")
    return _apresentar({**pessoa, "mfa_ok": False})


@router.get("")
def quem_sou(sessao=Depends(sessao_atual)):
    return _apresentar(sessao)


@router.delete("")
def sair(resposta: Response, sessao=Depends(sessao_atual)):
    with banco.transacao() as conn:
        conn.execute("DELETE FROM sessoes WHERE token_hash = %s", (sessao["token_hash"],))
    resposta.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/senha")
def trocar_senha(dados: TrocaDeSenha, sessao=Depends(sessao_atual)):
    with banco.transacao() as conn:
        if sessao["bloqueado_ate"] and _bloqueada(conn, sessao["id"]):
            raise _MUITAS
        try:
            hasher.verify(sessao["senha_hash"], dados.senha_atual)
        except (VerificationError, InvalidHashError):
            _falha(conn, sessao["id"])
            certo = False
        else:
            certo = True
            conn.execute("UPDATE pessoas SET senha_hash = %s, trocar_senha = false, falhas = 0 WHERE id = %s",
                         (hasher.hash(dados.senha_nova), sessao["id"]))
            conn.execute("DELETE FROM sessoes WHERE pessoa_id = %s AND token_hash <> %s", (sessao["id"], sessao["token_hash"]))
    if not certo:
        raise HTTPException(401, "Senha atual incorreta")
    return _apresentar({**sessao, "trocar_senha": False})


@router.get("/mfa")
def cadastro_mfa(sessao=Depends(sessao_atual)):
    if etapa(sessao) != "cadastrar_mfa":
        raise HTTPException(409, "Etapa incorreta")
    segredo = mfa.novo_segredo()
    with banco.transacao() as conn:
        conn.execute("UPDATE pessoas SET mfa_segredo = %s WHERE id = %s", (mfa.selar(sessao["id"], segredo), sessao["id"]))
    svg = mfa.qr_em_svg(mfa.endereco(segredo, sessao["email"]))
    return {"segredo": segredo, "qr": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()}


@router.post("/mfa/ativar")
def ativar_mfa(dados: Codigo, sessao=Depends(sessao_atual)):
    if etapa(sessao) != "cadastrar_mfa" or not sessao["mfa_segredo"]:
        raise HTTPException(409, "Etapa incorreta")
    passo = mfa.passo_do_codigo(mfa.abrir(sessao["id"], sessao["mfa_segredo"]), dados.codigo)
    if passo is None:
        raise HTTPException(401, "Código incorreto")
    codigos = mfa.novos_codigos_de_recuperacao()
    with banco.transacao() as conn:
        conn.execute("UPDATE pessoas SET mfa_ativo = true, mfa_ultimo_passo = %s WHERE id = %s", (passo, sessao["id"]))
        conn.execute("DELETE FROM codigos_recuperacao WHERE pessoa_id = %s", (sessao["id"],))
        for codigo in codigos:
            conn.execute("INSERT INTO codigos_recuperacao (pessoa_id, codigo_hash) VALUES (%s, %s)",
                         (sessao["id"], mfa.hash_do_codigo(sessao["id"], codigo)))
        conn.execute("UPDATE sessoes SET mfa_ok = true WHERE token_hash = %s", (sessao["token_hash"],))
    return {**_apresentar({**sessao, "mfa_ativo": True, "mfa_ok": True}), "codigos_recuperacao": codigos}


@router.post("/mfa")
def conferir_mfa(dados: Codigo, sessao=Depends(sessao_atual)):
    if etapa(sessao) != "codigo_mfa":
        raise HTTPException(409, "Etapa incorreta")
    with banco.transacao() as conn:
        # A trava impede o mesmo código de valer em dois pedidos simultâneos.
        pessoa = conn.execute("SELECT * FROM pessoas WHERE id = %s FOR UPDATE", (sessao["id"],)).fetchone()
        if pessoa["bloqueado_ate"] and _bloqueada(conn, sessao["id"]):
            raise _MUITAS
        passo = mfa.passo_do_codigo(mfa.abrir(sessao["id"], pessoa["mfa_segredo"]), dados.codigo)
        certo = passo is not None and passo > (pessoa["mfa_ultimo_passo"] or 0)
        if certo:
            conn.execute("UPDATE pessoas SET mfa_ultimo_passo = %s WHERE id = %s", (passo, sessao["id"]))
        elif passo is None:
            certo = conn.execute(
                """UPDATE codigos_recuperacao SET usado_em = now()
                   WHERE pessoa_id = %s AND codigo_hash = %s AND usado_em IS NULL RETURNING 1""",
                (sessao["id"], mfa.hash_do_codigo(sessao["id"], dados.codigo))).fetchone() is not None
        if certo:
            conn.execute("UPDATE pessoas SET falhas = 0 WHERE id = %s", (sessao["id"],))
            conn.execute("UPDATE sessoes SET mfa_ok = true WHERE token_hash = %s", (sessao["token_hash"],))
        else:
            _falha(conn, sessao["id"])
    if not certo:
        raise HTTPException(401, "Código incorreto")
    return _apresentar({**sessao, "mfa_ok": True})

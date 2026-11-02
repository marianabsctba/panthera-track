import os
from contextlib import contextmanager

from cryptography.fernet import Fernet
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(os.environ["TRACK_BANCO_DSN"], min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


def fechar():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transacao(cliente_id=None):
    """Sem cliente na transação, as tabelas de chamados não devolvem nada."""
    with pool().connection() as conn, conn.transaction():
        if cliente_id is not None:
            conn.execute("SELECT set_config('track.cliente', %s, true)", (str(cliente_id),))
        yield conn


def _cofre():
    return Fernet(os.environ["TRACK_CHAVE"].encode())


def cifrar(texto):
    return _cofre().encrypt(texto.encode()).decode()


def decifrar(texto):
    return _cofre().decrypt(texto.encode()).decode()

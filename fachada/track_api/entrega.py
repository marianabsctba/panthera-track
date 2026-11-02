"""Entrega os eventos ao sistema que abriu o chamado, em ordem e com retentativa."""
import hashlib
import hmac
import json
import logging
import threading
import time

import httpx

from .banco import decifrar, transacao

log = logging.getLogger("track.entrega")
_ESPERA_MAXIMA = 3600


def assinar(segredo, carimbo, corpo):
    return hmac.new(segredo.encode(), f"{carimbo}.".encode() + corpo, hashlib.sha256).hexdigest()


def _enviar(http, cliente, segredo, evento):
    corpo = json.dumps({
        "evento_id": str(evento["id"]),
        "sequencia": evento["sequencia"],
        "tipo": evento["tipo"],
        "autor": evento["autor"],
        "em": evento["criado_em"].isoformat(timespec="seconds"),
        "chamado": evento["dados"],
    }, ensure_ascii=False).encode()
    carimbo = str(int(time.time()))
    r = http.post(cliente["webhook_url"], content=corpo, headers={
        "Content-Type": "application/json",
        "X-Track-Data": carimbo,
        "X-Track-Assinatura": assinar(segredo, carimbo, corpo),
    })
    return r.status_code < 300


def entregar_pendentes():
    with transacao() as conn:
        clientes = conn.execute("SELECT * FROM clientes WHERE webhook_url IS NOT NULL").fetchall()
    with httpx.Client(timeout=5) as http:
        for cliente in clientes:
            segredo = decifrar(cliente["webhook_segredo"])
            with transacao(cliente["id"]) as conn:
                pendentes = conn.execute(
                    """SELECT * FROM eventos WHERE entregue_em IS NULL
                       ORDER BY numero, sequencia LIMIT 100 FOR UPDATE SKIP LOCKED""").fetchall()
                travados = set()
                for ev in pendentes:
                    if ev["numero"] in travados:
                        continue
                    if ev["proxima_tentativa"].timestamp() > time.time():
                        travados.add(ev["numero"])
                        continue
                    try:
                        ok = _enviar(http, cliente, segredo, ev)
                    except httpx.HTTPError:
                        ok = False
                    if ok:
                        conn.execute("UPDATE eventos SET entregue_em = now() WHERE id = %s", (ev["id"],))
                    else:
                        # Sem entregar este, os seguintes do mesmo chamado esperam: a ordem importa.
                        travados.add(ev["numero"])
                        espera = min(2 ** ev["tentativas"], _ESPERA_MAXIMA)
                        conn.execute(
                            """UPDATE eventos SET tentativas = tentativas + 1,
                               proxima_tentativa = now() + make_interval(secs => %s) WHERE id = %s""",
                            (espera, ev["id"]))


def iniciar(parar):
    def laco():
        while not parar.wait(1):
            try:
                entregar_pendentes()
            except Exception:
                log.exception("falha na entrega de eventos")
    t = threading.Thread(target=laco, daemon=True, name="entrega")
    t.start()
    return t

"""Conversa com a instância de GLPI de um cliente. Nada daqui chega cru à resposta da API."""
import threading
import time

import httpx

from .banco import decifrar

PERFIL_FACHADA = "6"  # Technician, na instalação padrão


class MotorIndisponivel(Exception):
    pass


class Motor:
    def __init__(self, url, oauth_id, oauth_segredo, usuario, senha, perfil=None):
        self._base = url.rstrip("/") + "/api.php"
        self._perfil = perfil
        self._credenciais = {
            "grant_type": "password", "client_id": oauth_id, "client_secret": oauth_segredo,
            "username": usuario, "password": senha, "scope": "api",
        }
        self._http = httpx.Client(timeout=30)
        self._token = None
        self._expira = 0
        self._trava = threading.Lock()

    def _obter_token(self, renovar=False):
        with self._trava:
            if renovar or not self._token or time.time() >= self._expira:
                r = self._http.post(self._base + "/token", data=self._credenciais)
                if r.status_code != 200:
                    raise MotorIndisponivel(f"token: {r.status_code}")
                dados = r.json()
                self._token = dados["access_token"]
                self._expira = time.time() + int(dados.get("expires_in", 3600)) - 60
            return self._token

    def pedir(self, metodo, caminho, **k):
        cabecalhos = {"Authorization": "Bearer " + self._obter_token(), "GLPI-Entity": "0"}
        if self._perfil:
            # Sem isto, a sessão usa o perfil padrão do usuário novo, de autoatendimento.
            cabecalhos["GLPI-Profile"] = self._perfil
        try:
            r = self._http.request(metodo, f"{self._base}/v3{caminho}", headers=cabecalhos, **k)
            if r.status_code == 401:
                cabecalhos["Authorization"] = "Bearer " + self._obter_token(renovar=True)
                r = self._http.request(metodo, f"{self._base}/v3{caminho}", headers=cabecalhos, **k)
        except httpx.HTTPError as e:
            raise MotorIndisponivel(str(e)) from e
        if r.status_code >= 300:
            raise MotorIndisponivel(f"{metodo} {caminho}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def criar_chamado(self, **campos):
        return self.pedir("POST", "/Assistance/Ticket", json=campos)["id"]

    def chamado(self, numero):
        return self.pedir("GET", f"/Assistance/Ticket/{numero}")

    def buscar_externo(self, id_externo):
        achados = self.pedir("GET", "/Assistance/Ticket", params={"filter": f"external_id=={id_externo}"})
        return achados[0]["id"] if achados else None

    def mudar_status(self, numero, status):
        self.pedir("PATCH", f"/Assistance/Ticket/{numero}", json={"status": status})

    def acompanhamento(self, numero, texto):
        self.pedir("POST", f"/Assistance/Ticket/{numero}/Timeline/Followup", json={"content": texto})

    def solucao(self, numero, texto):
        self.pedir("POST", f"/Assistance/Ticket/{numero}/Timeline/Solution", json={"content": texto})

    def linha_do_tempo(self, numero):
        return self.pedir("GET", f"/Assistance/Ticket/{numero}/Timeline")


_motores = {}
_trava = threading.Lock()


def motor_do_cliente(cliente):
    with _trava:
        m = _motores.get(cliente["id"])
        if m is None:
            m = Motor(cliente["motor_url"], cliente["motor_oauth_id"], decifrar(cliente["motor_oauth_segredo"]),
                      cliente["motor_usuario"], decifrar(cliente["motor_senha"]), perfil=PERFIL_FACHADA)
            _motores[cliente["id"]] = m
        return m

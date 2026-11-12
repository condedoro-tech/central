"""Monta o index.html: cifra conteudo/central.html com a senha e embute na tela de senha.

Uso: CENTRAL_SENHA=... python ferramentas/montar.py
     CENTRAL_SENHA=... python ferramentas/montar.py --sal-novo   gera sal novo (troca de senha; regrave tendencias.json depois)

O sal é público e fica no index.html. Ele é mantido entre montagens para que tendencias.json,
cifrado pelo GitHub com a mesma senha, continue abrindo. AES-256-GCM, chave por PBKDF2-SHA256.
"""
import base64
import hashlib
import os
import re
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITERACOES = 600000


def main():
    senha = os.environ.get("CENTRAL_SENHA", "").strip()
    if not senha:
        sys.exit("Defina CENTRAL_SENHA.")
    destino = os.path.join(RAIZ, "index.html")
    sal = None
    if os.path.exists(destino) and "--sal-novo" not in sys.argv:
        achado = re.search(r'var SAL="([^"]+)"', open(destino, encoding="utf-8").read())
        sal = base64.b64decode(achado.group(1)) if achado else None
    sal = sal or os.urandom(16)
    chave = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES, 32)
    iv = os.urandom(12)
    conteudo = open(os.path.join(RAIZ, "conteudo", "central.html"), encoding="utf-8").read()
    dados = AESGCM(chave).encrypt(iv, conteudo.encode("utf-8"), None)
    modelo = open(os.path.join(RAIZ, "ferramentas", "modelo-senha.html"), encoding="utf-8").read()
    pagina = (modelo.replace("__SAL__", base64.b64encode(sal).decode())
                    .replace("__IV__", base64.b64encode(iv).decode())
                    .replace("__DADOS__", base64.b64encode(dados).decode())
                    .replace("__IT__", str(ITERACOES)))
    open(destino, "w", encoding="utf-8").write(pagina)
    print("index.html montado:", len(pagina) // 1024, "KB")


if __name__ == "__main__":
    main()

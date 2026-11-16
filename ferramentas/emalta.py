"""'Em alta hoje' (guia do Bruno) — Fase 3/4, roda em MODO SOMBRA por padrão.

Cruza demanda (Google Trends, já coletado em tendencias.json) com imprensa (Google Notícias RSS when:6h)
e, para cada assunto quente, diz se o UOL já cobriu (matching DETERMINÍSTICO nas últimas 12h). O Haiku só
redige os textos (título, por quê, correr atrás); números, temperatura e status do UOL vêm do código.

Sombra (EM_ALTA_SOMBRA=1, padrão): calcula e imprime um resumo; NÃO mexe na página. Tira a sombra só depois
que o Bruno aprovar (Fase 5).

Fontes gratuitas, sem chave. GDELT ficou adiado (redundante com o Google Notícias).
Uso: CENTRAL_SENHA=... ANTHROPIC_API_KEY=... python ferramentas/emalta.py
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

from tendencias import BRT, agora, baixar, cifrar, limpar, norm

MODELO = "claude-haiku-5-5"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Config (o Bruno edita). Limites iniciais, a calibrar na fase sombra.
CONFIG = {"janela_horas": 6, "janela_uol_horas": 12, "min_veiculos": 4, "max_cards": 8, "max_novos": 5}

# Entidades fixas: 5 clubes + Seleção. O resto vem da descoberta.
ENTIDADES = [
    {"nome": "Corinthians", "tipo": "clube", "aliases": ["corinthians", "timao"], "consulta": '"Corinthians"'},
    {"nome": "Flamengo", "tipo": "clube", "aliases": ["flamengo", "mengao"], "consulta": '"Flamengo"'},
    {"nome": "Palmeiras", "tipo": "clube", "aliases": ["palmeiras", "verdao"], "consulta": '"Palmeiras"'},
    {"nome": "São Paulo", "tipo": "clube", "aliases": ["sao paulo fc", "tricolor paulista"],
     "consulta": '"São Paulo FC" OR "São Paulo" futebol', "exigir_contexto": ["fc", "futebol", "tricolor", "morumbi", "brasileirao", "libertadores"]},
    {"nome": "Santos", "tipo": "clube", "aliases": ["santos fc", "peixe"],
     "consulta": '"Santos FC" OR "Santos" futebol', "exigir_contexto": ["fc", "futebol", "peixe", "vila belmiro", "neymar"]},
    {"nome": "Seleção Brasileira", "tipo": "selecao", "aliases": ["selecao brasileira", "selecao", "canarinho"], "consulta": '"Seleção Brasileira"'},
]


# ---------------------------------------------------------------- fontes
def gnews(consulta, horas, site=None):
    """Google Notícias RSS: devolve itens com veículo (do <source>), título e url."""
    q = consulta + (f" site:{site}" if site else "") + f" when:{horas}h"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(q)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    xml = baixar(url) or ""
    itens = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        m_s = re.search(r'<source url="[^"]*">(.*?)</source>', it, re.S)
        m_t = re.search(r"<title>(.*?)</title>", it, re.S)
        m_u = re.search(r"<link>(.*?)</link>", it, re.S)
        if not m_t:
            continue
        titulo = re.sub(r"\s-\s[^-]+$", "", limpar(m_t.group(1)))
        itens.append({"titulo": titulo, "veiculo": (limpar(m_s.group(1)) if m_s else "?"),
                      "url": limpar(m_u.group(1)) if m_u else ""})
    return itens


def trends_termos():
    """Lê o tendencias.json (cifrado) e devolve os termos de esporte em alta, separando jogos."""
    caminho = os.path.join(RAIZ, "tendencias.json")
    if not os.path.exists(caminho):
        return [], "tendencias.json ausente"
    try:
        senha = os.environ["CENTRAL_SENHA"].strip()
        idx = open(os.path.join(RAIZ, "index.html"), encoding="utf-8").read()
        sal = base64.b64decode(re.search(r'var SAL="([^"]+)"', idx).group(1))
        chave = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, 600000, 32)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        d = json.load(open(caminho))
        r = json.loads(AESGCM(chave).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["dados"]), None).decode())
    except Exception as e:
        return [], f"falhou ao abrir tendencias.json: {str(e)[:50]}"
    vistos, termos = set(), []
    for h in ("4", "24"):
        for it in (r.get("trends", {}).get(h, {}).get("itens", []) or []):
            t = it.get("termo", "").strip()
            if not t or norm(t) in vistos:
                continue
            vistos.add(norm(t))
            jogo = bool(re.search(r"\s(x|vs)\s", norm(t)))
            termos.append({"termo": t, "volume": it.get("volume", ""), "jogo": jogo})
    return termos, None


# ---------------------------------------------------------------- casamento e UOL
def casa_alias(entidade, texto):
    alvo = norm(texto)
    bate = any(re.search(r"\b" + re.escape(a) + r"\b", alvo) for a in entidade["aliases"]) or \
        re.search(r"\b" + re.escape(norm(entidade["nome"])) + r"\b", alvo)
    if bate and entidade.get("exigir_contexto"):
        return any(c in alvo for c in entidade["exigir_contexto"])
    return bool(bate)


def sinal_busca(entidade, termos):
    for t in termos:
        if not t["jogo"] and casa_alias(entidade, t["termo"]):
            return {"presente": True, "volume": t["volume"], "termo": t["termo"]}
    return {"presente": False, "volume": "", "termo": ""}


def uol_status(entidade):
    itens = gnews(entidade["consulta"], CONFIG["janela_uol_horas"], site="uol.com.br/esporte")
    fortes = [i for i in itens if casa_alias(entidade, i["titulo"])]
    if fortes:
        return {"status": "ja_deu", "links": [i["url"] for i in fortes[:3] if i["url"]]}
    if itens:
        return {"status": "fraco", "links": [i["url"] for i in itens[:1] if i["url"]]}
    return {"status": "nao_localizado", "links": []}


# ---------------------------------------------------------------- Haiku (só texto)
def _claude(sistema, texto, max_tokens=2000):
    from editor import chamar_claude, SISTEMA_CORRER  # reaproveita o cliente/retry
    # chamar_claude espera (texto, sistema); monta JSON
    return chamar_claude(texto, sistema)


def descobrir(termos_sem_entidade):
    if not termos_sem_entidade:
        return []
    lista = "\n".join(f"- {t['termo']}" for t in termos_sem_entidade[:20])
    sis = ('Classifique cada termo de busca em alta como esporte brasileiro ou não. Responda APENAS JSON: '
           '{"itens":[{"termo":"","classe":"entidade_esportiva|jogo|nao_esporte|incerto","entidade_sugerida":""}]}. '
           'entidade_esportiva = time, atleta, técnico, competição ou assunto esportivo que renda matéria. '
           'jogo = confronto/partida. Use "incerto" quando não der para saber.')
    try:
        r = _claude(sis, "Termos:\n" + lista)
    except Exception as e:
        print("  (descoberta falhou:", str(e)[:50], ")")
        return []
    cand = []
    for it in r.get("itens", []):
        if it.get("classe") == "entidade_esportiva" and it.get("termo"):
            nome = it.get("entidade_sugerida") or it["termo"]
            cand.append({"nome": nome, "tipo": "descoberta", "aliases": [norm(nome)], "consulta": f'"{nome}"'})
        if len(cand) >= CONFIG["max_novos"]:
            break
    return cand


def redigir(assuntos):
    """Uma chamada: para cada assunto, gera titulo/por_que/correr_atras. Não altera números."""
    compact = [{"i": n, "entidade": a["entidade"], "temperatura": a["temperatura"],
                "sinais": a["sinais"], "uol": a["uol"]["status"],
                "manchetes": [m["titulo"] for m in a["manchetes"][:6]]} for n, a in enumerate(assuntos)]
    sis = ('Você redige cards curtos para a redação do UOL Esporte a partir de dados de assuntos em alta. '
           'Use APENAS as manchetes e números dados; não acrescente fato, data, cargo, idade ou clube. '
           'Sem linha fina. Para cada item responda: "titulo" (até 12 palavras, direto), "por_que" (1 frase até '
           '20 palavras citando os sinais de busca e/ou imprensa; se só houver um sinal, comece com "Sinal fraco:"), '
           '"correr_atras" (1 a 2 bullets acionáveis). Se não der para entender pelas manchetes, '
           '"titulo":"Assunto não identificado" e correr_atras []. '
           'Responda APENAS JSON: {"cards":[{"i":0,"titulo":"","por_que":"","correr_atras":[""]}]}')
    try:
        r = _claude(sis, json.dumps({"assuntos": compact}, ensure_ascii=False))
        por_i = {c.get("i"): c for c in r.get("cards", [])}
    except Exception as e:
        print("  (redação falhou, usando contingência:", str(e)[:50], ")")
        por_i = {}
    for n, a in enumerate(assuntos):
        c = por_i.get(n) or {}
        a["titulo"] = c.get("titulo") or (a["entidade"] + ": " + (a["manchetes"][0]["titulo"] if a["manchetes"] else "assunto em alta"))
        a["por_que"] = c.get("por_que") or ""
        a["correr_atras"] = c.get("correr_atras") or []
    return assuntos


# ---------------------------------------------------------------- principal
def montar():
    termos, erro_trends = trends_termos()
    fontes_fora = []
    if erro_trends:
        fontes_fora.append(f"Trends: {erro_trends}")
    candidatos = list(ENTIDADES)
    # descoberta: termos do Trends sem entidade fixa e que não são jogo
    sem_ent = [t for t in termos if not t["jogo"] and not any(casa_alias(e, t["termo"]) for e in ENTIDADES)]
    candidatos += descobrir(sem_ent)

    assuntos = []
    for ent in candidatos:
        busca = sinal_busca(ent, termos)
        try:
            imp = gnews(ent["consulta"], CONFIG["janela_horas"])
            veic = len({norm(i["veiculo"]) for i in imp if i["veiculo"] != "?"})
            fora = []
        except Exception as e:
            imp, veic, fora = [], 0, [f"Google Notícias: {str(e)[:40]}"]
        imprensa_alta = veic >= CONFIG["min_veiculos"]
        if not (busca["presente"] or imprensa_alta):
            continue
        temperatura = "quente" if (busca["presente"] and imprensa_alta) else "morno"
        try:
            uol = uol_status(ent)
        except Exception:
            uol = {"status": "nao_localizado", "links": []}
        assuntos.append({
            "entidade": ent["nome"], "origem": "descoberta" if ent["tipo"] == "descoberta" else "lista",
            "temperatura": temperatura,
            "sinais": {"busca": busca, "imprensa": {"veiculos_distintos": veic, "janela_horas": CONFIG["janela_horas"]}},
            "manchetes": [{"titulo": i["titulo"], "veiculo": i["veiculo"]} for i in imp[:6]],
            "uol": uol, "fontes_indisponiveis": fora + fontes_fora,
        })
        time.sleep(1.2)

    # ordena: quente antes de morno; dentro, UOL não localizado primeiro, depois mais imprensa
    ordem_temp = {"quente": 0, "morno": 1}
    ordem_uol = {"nao_localizado": 0, "fraco": 1, "ja_deu": 2}
    assuntos.sort(key=lambda a: (ordem_temp[a["temperatura"]], ordem_uol[a["uol"]["status"]],
                                 -a["sinais"]["imprensa"]["veiculos_distintos"]))
    assuntos = assuntos[:CONFIG["max_cards"]]
    if assuntos:
        redigir(assuntos)
    return assuntos, fontes_fora


def resumo_sombra(assuntos, fontes_fora):
    linhas = [f"# Em alta hoje — modo sombra ({agora().astimezone(BRT).isoformat(timespec='minutes')})", ""]
    if fontes_fora:
        linhas.append("Fontes indisponíveis: " + "; ".join(sorted(set(fontes_fora))))
    if not assuntos:
        linhas.append("Nenhum assunto cruzou os limites hoje.")
    for a in assuntos:
        b = a["sinais"]["busca"]; im = a["sinais"]["imprensa"]
        selo = {"ja_deu": "UOL já deu", "fraco": "UOL fraco", "nao_localizado": "UOL NÃO localizado"}[a["uol"]["status"]]
        linhas.append(f"\n## [{a['temperatura'].upper()}]{' [NOVO]' if a['origem']=='descoberta' else ''} {a.get('titulo','')}")
        linhas.append(f"- Entidade: {a['entidade']} | {selo}")
        linhas.append(f"- Busca (Trends): {'sim '+b['volume'] if b['presente'] else 'não'} | Imprensa: {im['veiculos_distintos']} veículos/{im['janela_horas']}h")
        if a.get("por_que"):
            linhas.append(f"- Por quê: {a['por_que']}")
        for c in a.get("correr_atras", []):
            linhas.append(f"- Correr atrás: {c}")
        for m in a["manchetes"][:3]:
            linhas.append(f"    · {m['veiculo']}: {m['titulo']}")
    return "\n".join(linhas)


def main():
    sombra = os.environ.get("EM_ALTA_SOMBRA", "1") != "0"
    assuntos, fontes_fora = montar()
    texto = resumo_sombra(assuntos, fontes_fora)
    print(texto)
    if sombra:
        print(f"\n[sombra] {len(assuntos)} assuntos calculados, nada publicado na página.")
        return
    # Fase 5 (ativação): gravar na página — a implementar quando o Bruno aprovar.
    print("[ativo] (gravação na página ainda não implementada; rode em sombra)")


if __name__ == "__main__":
    main()

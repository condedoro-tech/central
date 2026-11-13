"""Radar UOL Esporte — leitura comparativa UOL x concorrência (roda no GitHub Actions).

Duas análises, geradas a partir da coleta do noticiário:
  PARTE 1 — Balanço de ontem (dia fechado 00h-23h59): os assuntos mais importantes do dia e, em cada um,
            como o UOL se saiu frente à concorrência (liderou / em linha / atrás / furo da concorrência).
  PARTE 2 — Correr atrás hoje (00h até a hora da raspagem): o que a concorrência publicou e o UOL precisa
            perseguir. Sempre um card para cada um dos 5 (Corinthians, Flamengo, Palmeiras, São Paulo, Santos),
            depois outros assuntos; detalhe proporcional à importância.

Fontes: UOL (nosso veículo, coletado com reforço) e concorrência (ge, ESPN, Lance!, Terra, R7, Goal, TNT, Trivela).
Regra anti-invenção: todo item citado carrega veículo + URL reais do dossiê; o que não tem URL é descartado.
Hedge: quando o UOL não aparece no dossiê sobre um assunto, o card diz "não localizado no UOL (conferir)",
nunca "o UOL não deu".

Uso: CENTRAL_SENHA=... ANTHROPIC_API_KEY=... python ferramentas/editor.py            balanço de ontem + correr atrás hoje
     python ferramentas/editor.py --so-hoje                                           só a Parte 2 (hoje), mais rápido
     python ferramentas/editor.py --teste                                             imprime os dossiês (sem IA, sem senha)
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
from datetime import date, timedelta
from email.utils import parsedate_to_datetime

from tendencias import (BRT, CLUBES, GOOGLE, agora, baixar, cifrar, ge_home,
                        limpar, norm, uol_home, PARALELO)

# Modelo: haiku-5-5 (mais barato, p/ testes) < sonnet-5-5 (equilíbrio) < opus-5-5 (melhor análise).
MODELO = "claude-haiku-5-5"
ESFORCO = "medium"
FALLBACK_OK = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1"}

GRANDES = ["Corinthians", "Flamengo", "Palmeiras", "São Paulo", "Santos"]
# Concorrência (o UOL é tratado à parte, como nosso veículo).
PORTAIS = [
    ("ge", "ge.globo.com"), ("ESPN", "espn.com.br"), ("Lance!", "lance.com.br"),
    ("Terra", "terra.com.br/esportes"), ("R7", "esportes.r7.com"),
    ("Goal", "goal.com"), ("TNT Sports", "tntsports.com.br"), ("Trivela", "trivela.com.br"),
]
# Reforço de coleta do UOL (nosso ponto de referência): além da home, buscas por temas.
UOL_TEMAS = ["futebol", "seleção brasileira", "mercado da bola", "Brasileirão", "Libertadores", "Copa do Brasil", "CBF STJD"]


def busca(veiculo, site, termo, apelido=None, dias=1):
    """Busca no Google News por site, preservando a URL."""
    xml = baixar(GOOGLE.format(q=urllib.parse.quote(f"{termo} site:{site} when:{dias}d"))) or ""
    saida = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        m_data = re.search(r"<pubDate>(.*?)</pubDate>", item)
        m_tit = re.search(r"<title>(.*?)</title>", item, re.S)
        m_url = re.search(r"<link>(.*?)</link>", item, re.S)
        if not (m_data and m_tit and m_url):
            continue
        quando = parsedate_to_datetime(m_data.group(1)).astimezone(BRT)
        titulo = re.sub(r" - [^-]+$", "", limpar(m_tit.group(1)))
        if apelido and not re.search(apelido, titulo):
            continue
        saida.append({"veiculo": veiculo, "titulo": titulo, "url": limpar(m_url.group(1)),
                      "data": quando.date().isoformat()})
    return saida


def busca_clube(veiculo, site, clube, apelido, dias=1):
    return [dict(x, clube=clube) for x in busca(veiculo, site, f'"{clube}"', apelido, dias)]


def coletar_largo(alvos):
    """Coleta janela larga o bastante para cobrir as datas-alvo; devolve itens dessas datas (UOL marcado)."""
    hoje = agora().astimezone(BRT).date()
    dias = min(7, max(1, (hoje - min(date.fromisoformat(a) for a in alvos)).days + 1))
    alvos = set(alvos)
    tarefas = [("ge home", lambda: [{"veiculo": "ge", "titulo": i["titulo"], "url": i["url"], "data": i["quando"][:10]} for i in ge_home()]),
               ("UOL home", lambda: [{"veiculo": "UOL", "titulo": i["titulo"], "url": i["url"], "data": i["quando"][:10]} for i in uol_home()])]
    # concorrência: futebol + seleção + 5 grandes
    for veiculo, site in PORTAIS:
        tarefas.append((f"{veiculo} futebol", lambda v=veiculo, s=site: busca(v, s, "futebol", dias=dias)))
        tarefas.append((f"{veiculo} seleção", lambda v=veiculo, s=site: busca(v, s, "seleção brasileira", dias=dias)))
        for clube in GRANDES:
            apelido = CLUBES[clube][1]
            tarefas.append((f"{veiculo} {clube}", lambda v=veiculo, s=site, c=clube, a=apelido: busca_clube(v, s, c, a, dias)))
    # reforço UOL: temas + 5 grandes
    for tema in UOL_TEMAS:
        tarefas.append((f"UOL {tema}", lambda t=tema: busca("UOL", "uol.com.br/esporte", t, dias=dias)))
    for clube in GRANDES:
        apelido = CLUBES[clube][1]
        tarefas.append((f"UOL {clube}", lambda c=clube, a=apelido: busca_clube("UOL", "uol.com.br/esporte", c, a, dias)))
    itens, falhas = [], []
    with cf.ThreadPoolExecutor(PARALELO) as ex:
        futuros = {ex.submit(f): n for n, f in tarefas}
        for fut in cf.as_completed(futuros):
            try:
                itens.extend(fut.result() or [])
            except Exception as erro:
                falhas.append(f"{futuros[fut]}: {str(erro)[:80]}")
    vistos, unicos = set(), []
    for i in itens:
        if i.get("data") not in alvos:
            continue
        chave = (i["data"], i["veiculo"], norm(i["titulo"]))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(i)
    return unicos, falhas


def dossie(itens):
    """Dossiê com o UOL destacado como nosso veículo, concorrência por veículo, e blocos por clube e seleção."""
    linhas = []
    uol = [i for i in itens if i["veiculo"] == "UOL"]
    linhas.append(f"### NOSSO VEÍCULO — UOL ({len(uol)}) ###")
    for i in uol[:60]:
        linhas.append(f"[UOL {i['data']}] {i['titulo']} | {i['url']}")
    linhas.append("\n### CONCORRÊNCIA (por veículo) ###")
    por_veiculo = {}
    for i in itens:
        if i["veiculo"] != "UOL":
            por_veiculo.setdefault(i["veiculo"], []).append(i)
    for v, lst in por_veiculo.items():
        linhas.append(f"\n-- {v} ({len(lst)}) --")
        for i in lst[:35]:
            linhas.append(f"[{i['data']}] {i['titulo']} | {i['url']}")
    for clube in GRANDES:
        rel = [i for i in itens if i.get("clube") == clube or re.search(CLUBES[clube][1], i["titulo"])]
        linhas.append(f"\n### CLUBE: {clube.upper()} ({len(rel)}) — UOL e concorrência ###")
        for i in rel[:30]:
            linhas.append(f"[{i['veiculo']} {i['data']}] {i['titulo']} | {i['url']}")
    sel = [i for i in itens if re.search(r"sele[çc][ãa]o|CBF|Ancelotti", i["titulo"])]
    linhas.append(f"\n### SELEÇÃO ({len(sel)}) — UOL e concorrência ###")
    for i in sel[:25]:
        linhas.append(f"[{i['veiculo']} {i['data']}] {i['titulo']} | {i['url']}")
    return "\n".join(linhas)


COMUM = """No dossiê, o bloco "NOSSO VEÍCULO — UOL" é o UOL; todos os outros (ge, ESPN, Lance!, Terra, R7, Goal, TNT Sports, Trivela) são CONCORRÊNCIA.
Regras invioláveis:
- Use SOMENTE itens do dossiê. Toda URL citada é copiada EXATA do dossiê. Item sem URL real não entra.
- Descartar: "onde assistir", calendário, regulamento, retrospectiva, "tudo sobre".
- Hedge do UOL: só diga que o UOL deu (uolDeu=true, com uolUrl do bloco UOL) se achar a matéria no bloco UOL. Se não achar, uolDeu=false (= "não localizado no UOL, conferir"). NUNCA afirme que o UOL não deu."""

SISTEMA_BALANCO = COMUM + """
Tarefa: a partir do que saiu ONTEM, liste de 6 a 10 assuntos mais importantes do dia e avalie a cobertura do UOL frente à concorrência.
veredito, por assunto: "UOL liderou" (deu primeiro/mais completo), "UOL em linha" (deu junto da concorrência), "UOL atrás" (concorrência deu e o UOL não aparece ou deu pouco), "Furo da concorrência" (assunto forte que só a concorrência deu).
Responda APENAS JSON válido:
{"assuntos":[{"titulo":"","resumo":"","veredito":"","uolDeu":true,"uolUrl":"","concorrentes":[{"veiculo":"","url":""}],"comentario":"avaliação curta UOL x concorrência"}],
 "placar":"uma linha: onde o UOL ganhou e onde ficou atrás no dia"}"""

SISTEMA_CORRER = COMUM + """
Tarefa: montar a lista de prioridades do que o UOL precisa CORRER ATRÁS hoje, a partir do que a concorrência publicou até agora.
- SEMPRE um card para cada um dos 5, nesta ordem: Corinthians, Flamengo, Palmeiras, São Paulo, Santos. Sem novidade relevante: prioridade "baixa", novidade=false, resumo "sem novidade da concorrência; UOL em dia".
- Depois, "outros": assuntos importantes (seleção, mercado, tribunal, outro clube).
- prioridade "alta"/"media"/"baixa" conforme importância. Quanto mais importante, mais detalhe: em "alta" preencha concorrencia (o que o rival deu, com url) e apurar (o que o UOL deve correr atrás); em "baixa", só resumo curto.
Responda APENAS JSON válido:
{"clubes":[{"clube":"","prioridade":"","novidade":true,"titulo":"","resumo":"","concorrencia":[{"veiculo":"","url":"","oque":""}],"uolDeu":false,"uolUrl":"","apurar":[""]}],
 "outros":[{"tema":"","prioridade":"","titulo":"","resumo":"","concorrencia":[{"veiculo":"","url":"","oque":""}],"uolDeu":false,"uolUrl":"","apurar":[""]}]}"""


def chamar_claude(texto, sistema):
    from anthropic import Anthropic
    cliente = Anthropic()
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if MODELO in FALLBACK_OK else {}
    with cliente.beta.messages.stream(
        model=MODELO, max_tokens=32000, thinking={"type": "adaptive"},
        output_config={"effort": ESFORCO}, system=sistema,
        messages=[{"role": "user", "content": f"Dossiê:\n\n{texto}\n\nResponda no formato JSON pedido."}],
        **extra,
    ) as fluxo:
        msg = fluxo.get_final_message()
    bruto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    i, f = bruto.find("{"), bruto.rfind("}")
    return json.loads(bruto[i:f + 1])


def _ancora(obj, urls):
    """Normaliza um item com concorrentes/uol: filtra urls inválidas; uolDeu só com uolUrl real."""
    chave = "concorrentes" if "concorrentes" in obj else "concorrencia"
    obj[chave] = [c for c in obj.get(chave, []) if c.get("url") in urls]
    if not (obj.get("uolDeu") and obj.get("uolUrl") in urls):
        obj["uolDeu"] = False
        obj["uolUrl"] = ""
    return obj


def balanco_do_dia(itens_dia):
    rel = chamar_claude(dossie(itens_dia), SISTEMA_BALANCO)
    urls = {i["url"] for i in itens_dia if i.get("url")}
    assuntos = [_ancora(a, urls) for a in rel.get("assuntos", [])]
    assuntos = [a for a in assuntos if a["concorrentes"] or a["uolDeu"]]
    return {"assuntos": assuntos, "placar": rel.get("placar", "")}


def correr_atras(itens_dia):
    rel = chamar_claude(dossie(itens_dia), SISTEMA_CORRER)
    urls = {i["url"] for i in itens_dia if i.get("url")}
    clubes = {c.get("clube"): _ancora(c, urls) for c in rel.get("clubes", [])}
    ordenados = [clubes.get(nome, {"clube": nome, "prioridade": "baixa", "novidade": False,
                 "titulo": nome, "resumo": "Sem leitura nesta rodada.", "concorrencia": [], "uolDeu": False, "apurar": []})
                 for nome in GRANDES]
    outros = [_ancora(o, urls) for o in rel.get("outros", [])]
    outros = [o for o in outros if o["concorrencia"] or o["uolDeu"]]
    return {"clubes": ordenados, "outros": outros}


def datas_alvo():
    """Dias do balanço: na segunda lê sexta, sábado e domingo; nos demais dias úteis, ontem."""
    hoje = agora().astimezone(BRT).date()
    if hoje.weekday() == 0:
        return [(hoje - timedelta(days=d)).isoformat() for d in (3, 2, 1)]
    return [(hoje - timedelta(days=1)).isoformat()]


def main():
    teste = "--teste" in sys.argv
    so_hoje = "--so-hoje" in sys.argv
    override = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--datas=")), "")
    hoje = agora().astimezone(BRT).date().isoformat()
    dias_balanco = [] if so_hoje else (override.split(",") if override else datas_alvo())
    alvos = sorted(set(dias_balanco) | {hoje})
    itens, falhas = coletar_largo(alvos)

    if teste:
        for d in alvos:
            print(f"\n######## {d} ########")
            print(dossie([i for i in itens if i["data"] == d]))
        print(f"\n== {len(itens)} itens | balanço {dias_balanco} | hoje {hoje} | falhas {len(falhas)} ==")
        return

    balanco = []
    for d in dias_balanco:
        itens_d = [i for i in itens if i["data"] == d]
        if itens_d:
            b = balanco_do_dia(itens_d)
            balanco.append({"data": d, **b})
    itens_hoje = [i for i in itens if i["data"] == hoje]
    parte2 = correr_atras(itens_hoje) if itens_hoje else {"clubes": [], "outros": []}

    relatorio = {
        "geradoEm": agora().astimezone(BRT).isoformat(timespec="minutes"),
        "balanco": balanco,
        "correrAtras": {"data": hoje, "parcial": True, **parte2},
        "totalItens": len(itens),
        "fontesForaDoAlcance": ["redes sociais (não monitoradas)", "CazéTV e blogs independentes"],
    }
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json.dump(cifrar(json.dumps(relatorio, ensure_ascii=False)), open(os.path.join(raiz, "editorial.json"), "w", encoding="utf-8"))
    print(f"editorial.json gravado | balanço {[b['data'] for b in balanco]} | hoje {hoje} | {len(itens)} itens | {MODELO}/{ESFORCO} | falhas {len(falhas)}")


if __name__ == "__main__":
    main()

"""Leitura editorial do dia da Central (roda 1x/dia no GitHub Actions).

Faz uma coleta larga do noticiário de ONTEM (ge, UOL, ESPN, Lance!, Terra, R7, Goal, TNT Sports, Trivela),
monta um dossiê com veículo + data + URL de cada item e pede ao Claude uma leitura editorial — concorrência,
5 grandes clubes e seleção — SÓ sobre os itens coletados. Grava editorial.json cifrado com a chave da Central.

Regras herdadas do pedido do dono:
- Janela: 00h00 às 23h59 de ONTEM (BRT). Item fora da janela é descartado.
- Todo item citado carrega veículo, data e URL reais; o que não tem os três é descartado (anti-invenção).
- Cotas são tetos, não pisos. "Dia fraco" é resposta válida.
- Não monitora redes sociais: a Parte 2 do pedido foi deliberadamente cortada.

Uso: CENTRAL_SENHA=... ANTHROPIC_API_KEY=... python ferramentas/editor.py     grava editorial.json cifrado
     python ferramentas/editor.py --teste                                       só monta e imprime o dossiê (sem IA, sem senha)
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
from datetime import timedelta
from email.utils import parsedate_to_datetime

from tendencias import (BRT, CLUBES, GOOGLE, agora, baixar, cifrar, ge_home,
                        ge_pagina, limpar, norm, uol_home, PARALELO)

# Troque MODELO para "claude-sonnet-5-5" para gastar ~metade (qualidade um pouco menor, ainda ótima).
MODELO = "claude-opus-5-5"
ESFORCO = "medium"          # low|medium|high|xhigh|max — mais alto = melhor análise e mais caro

# Os 5 grandes do pedido (Parte 3). Reusa o apelido (regex) que já está em CLUBES.
GRANDES = ["Corinthians", "Flamengo", "Palmeiras", "São Paulo", "Santos"]
# Fontes: as do Igor (ge/UOL/ESPN) + as novas que dá para coletar por busca de site.
PORTAIS = [
    ("ge", "ge.globo.com"), ("UOL", "uol.com.br/esporte"), ("ESPN", "espn.com.br"),
    ("Lance!", "lance.com.br"), ("Terra", "terra.com.br/esportes"), ("R7", "esportes.r7.com"),
    ("Goal", "goal.com"), ("TNT Sports", "tntsports.com.br"), ("Trivela", "trivela.com.br"),
]
# Para o bloco "fora do futebol": outros esportes, nos 3 portais com cobertura ampla.
OUTROS = ["NBA", "Fórmula 1", "tênis", "vôlei", "UFC"]
PORTAIS_OUTROS = [("ge", "ge.globo.com"), ("UOL", "uol.com.br/esporte"), ("ESPN", "espn.com.br")]


def busca_clube(veiculo, site, clube, apelido):
    return [dict(x, clube=clube) for x in busca(veiculo, site, f'"{clube}"', apelido)]


def busca(veiculo, site, termo, apelido=None):
    """Busca no Google News por site, preservando a URL (diferente do coletor ao vivo)."""
    xml = baixar(GOOGLE.format(q=urllib.parse.quote(f"{termo} site:{site} when:1d"))) or ""
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


def coletar_largo():
    tarefas = [("ge home", lambda: [{"veiculo": "ge", "titulo": i["titulo"], "url": i["url"],
                                     "data": i["quando"][:10]} for i in ge_home()]),
               ("UOL home", lambda: [{"veiculo": "UOL", "titulo": i["titulo"], "url": i["url"],
                                     "data": i["quando"][:10]} for i in uol_home()])]
    for veiculo, site in PORTAIS:
        tarefas.append((f"{veiculo} futebol", lambda v=veiculo, s=site: busca(v, s, "futebol")))
        tarefas.append((f"{veiculo} seleção", lambda v=veiculo, s=site: busca(v, s, "seleção brasileira")))
        for clube in GRANDES:
            apelido = CLUBES[clube][1]
            tarefas.append((f"{veiculo} {clube}", lambda v=veiculo, s=site, c=clube, a=apelido: busca_clube(v, s, c, a)))
    for veiculo, site in PORTAIS_OUTROS:
        for termo in OUTROS:
            tarefas.append((f"{veiculo} {termo}", lambda v=veiculo, s=site, t=termo: [dict(x, naoFutebol=True) for x in busca(v, s, t)]))
    itens, falhas = [], []
    with cf.ThreadPoolExecutor(PARALELO) as ex:
        futuros = {ex.submit(f): n for n, f in tarefas}
        for fut in cf.as_completed(futuros):
            try:
                itens.extend(fut.result() or [])
            except Exception as erro:
                falhas.append(f"{futuros[fut]}: {str(erro)[:80]}")
    # só ONTEM (janela 00h-23h59 do dia anterior, BRT)
    ontem = (agora().astimezone(BRT) - timedelta(days=1)).date().isoformat()
    janela = [i for i in itens if i.get("data") == ontem]
    # tira repetidos (mesmo título normalizado no mesmo veículo)
    vistos, unicos = set(), []
    for i in janela:
        chave = (i["veiculo"], norm(i["titulo"]))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(i)
    return unicos, ontem, falhas


def dossie(itens):
    """Texto compacto por veículo + blocos de clubes e seleção, com veículo|data|url em cada linha."""
    linhas = []
    por_veiculo = {}
    for i in itens:
        por_veiculo.setdefault(i["veiculo"], []).append(i)
    linhas.append("=== NOTICIÁRIO DE FUTEBOL DE ONTEM POR VEÍCULO ===")
    for v, lst in por_veiculo.items():
        fut = [i for i in lst if not i.get("naoFutebol")]
        if not fut:
            continue
        linhas.append(f"\n-- {v} ({len(fut)}) --")
        for i in fut[:40]:
            linhas.append(f"[{i['data']}] {i['titulo']} | {i['url']}")
    for clube in GRANDES:
        rel = [i for i in itens if i.get("clube") == clube or re.search(CLUBES[clube][1], i["titulo"])]
        linhas.append(f"\n=== {clube.upper()} ({len(rel)}) ===")
        for i in rel[:25]:
            linhas.append(f"[{i['veiculo']} {i['data']}] {i['titulo']} | {i['url']}")
    sel = [i for i in itens if not i.get("naoFutebol") and re.search(r"sele[çc][ãa]o|CBF|Ancelotti|Brasil\b", i["titulo"])]
    linhas.append(f"\n=== SELEÇÃO BRASILEIRA ({len(sel)}) ===")
    for i in sel[:25]:
        linhas.append(f"[{i['veiculo']} {i['data']}] {i['titulo']} | {i['url']}")
    fora = [i for i in itens if i.get("naoFutebol")]
    linhas.append(f"\n=== FORA DO FUTEBOL (outros esportes) ({len(fora)}) ===")
    for i in fora[:25]:
        linhas.append(f"[{i['veiculo']} {i['data']}] {i['titulo']} | {i['url']}")
    return "\n".join(linhas)


SISTEMA = """Você faz a leitura editorial de esporte do UOL. Pensa como quem decide a capa: relevância, impacto, personagens e potencial de pauta. Mas escreve de forma executiva e direta, sem jargão interno.

Regras invioláveis:
- Use SOMENTE itens do dossiê que o usuário enviar. Nunca invente matéria, veículo, data ou URL. Todo item que você citar tem de copiar veículo, data e URL EXATOS do dossiê. Se não tiver os três, não cite.
- Janela: só ontem. O dossiê já está filtrado; não traga nada de fora.
- Descartar: retrospectiva, balanço, lista evergreen, "tudo sobre", "saiba quem é", "onde assistir", calendário, regulamento.
- Fato novo x contexto: um tema só entra se houver fato novo de ontem. Cota é teto, não piso — prefira curto e correto a longo e contaminado. "Dia fraco" é resposta válida: devolva lista curta ou vazia.
- Antes de fechar, releia cada item: data + veículo + fato novo têm de bater com o dossiê. Item que não passar, remova.

Responda APENAS com um JSON válido, sem texto antes ou depois, neste formato exato:
{
 "concorrencia": {
   "principais": [{"titulo","veiculo","data","url","porque"}],
   "furos": [{"titulo","veiculo","data","url","novo","porque"}],
   "podeVirarPauta": [{"titulo","veiculo","data","url","angulo"}],
   "foraDoFutebol": [{"titulo","veiculo","data","url","angulo"}]
 },
 "clubes": [{"clube","materias":[{"titulo","veiculo","data","url"}],"furos":[{"titulo","veiculo","data","url","novo"}],"pauta":{"ideia","porque"}}],
 "selecao": {"historias":[{"titulo","veiculo","data","url"}],"furo":{"titulo","veiculo","data","url","novo"},"leitura":{"risco","oportunidade","angulo"}},
 "fechamento": {"insight","alerta","aposta"}
}
Limites: principais até 10, furos até 5, podeVirarPauta até 5, foraDoFutebol até 5 (e nenhum sobre futebol). Em "clubes", um objeto para cada um dos 5 grandes, com até 5 matérias, até 3 furos e 1 pauta. Campos de texto curtos e concretos."""


def chamar_claude(texto):
    from anthropic import Anthropic
    cliente = Anthropic()
    with cliente.beta.messages.stream(
        model=MODELO, max_tokens=32000,
        thinking={"type": "adaptive"},
        output_config={"effort": ESFORCO},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=SISTEMA,
        messages=[{"role": "user", "content": f"Dossiê de ontem:\n\n{texto}\n\nFaça a leitura editorial no formato JSON pedido."}],
    ) as fluxo:
        msg = fluxo.get_final_message()
    bruto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    return json.loads(bruto[inicio:fim + 1])


def grounded(relatorio, urls_validas):
    """Remove qualquer item citado cuja URL não esteja no dossiê (barreira anti-invenção)."""
    def filtra(lista):
        return [x for x in lista if not x.get("url") or x["url"] in urls_validas]
    c = relatorio.get("concorrencia", {})
    for k in ("principais", "furos", "podeVirarPauta", "foraDoFutebol"):
        c[k] = filtra(c.get(k, []))
    for clube in relatorio.get("clubes", []):
        clube["materias"] = filtra(clube.get("materias", []))
        clube["furos"] = filtra(clube.get("furos", []))
    sel = relatorio.get("selecao", {})
    sel["historias"] = filtra(sel.get("historias", []))
    if sel.get("furo") and sel["furo"].get("url") and sel["furo"]["url"] not in urls_validas:
        sel["furo"] = None
    return relatorio


def main():
    teste = "--teste" in sys.argv
    itens, ontem, falhas = coletar_largo()
    texto = dossie(itens)
    if teste:
        print(texto)
        print(f"\n== {len(itens)} itens de ontem ({ontem}) | falhas: {len(falhas)} ==")
        for f in falhas[:8]:
            print("  -", f)
        return
    relatorio = chamar_claude(texto)
    relatorio = grounded(relatorio, {i["url"] for i in itens if i.get("url")})
    relatorio["geradoEm"] = agora().astimezone(BRT).isoformat(timespec="minutes")
    relatorio["janela"] = ontem
    relatorio["totalItens"] = len(itens)
    relatorio["fontesForaDoAlcance"] = ["CazéTV (canal de YouTube, sem portal de texto)", "blogs independentes (não enumeráveis)"]
    json.dump(cifrar(json.dumps(relatorio, ensure_ascii=False)), open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editorial.json"), "w", encoding="utf-8"))
    print(f"editorial.json gravado | janela {ontem} | {len(itens)} itens | modelo {MODELO}/{ESFORCO} | falhas {len(falhas)}")


if __name__ == "__main__":
    main()

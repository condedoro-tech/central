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

# Modelo: haiku-5-5 (mais barato, p/ testes) < sonnet-5-5 (equilíbrio) < opus-5-5 (melhor análise).
MODELO = "claude-haiku-5-5"
ESFORCO = "medium"          # low|medium|high|xhigh|max — mais alto = melhor análise e mais caro
# Modelos que aceitam o fallback de refusal da Anthropic (haiku não aceita).
FALLBACK_OK = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1"}

# Os 5 grandes do pedido (Parte 3). Reusa o apelido (regex) que já está em CLUBES.
GRANDES = ["Corinthians", "Flamengo", "Palmeiras", "São Paulo", "Santos"]
# Fontes: as do Igor (ge/UOL/ESPN) + as novas que dá para coletar por busca de site.
PORTAIS = [
    ("ge", "ge.globo.com"), ("UOL", "uol.com.br/esporte"), ("ESPN", "espn.com.br"),
    ("Lance!", "lance.com.br"), ("Terra", "terra.com.br/esportes"), ("R7", "esportes.r7.com"),
    ("Goal", "goal.com"), ("TNT Sports", "tntsports.com.br"), ("Trivela", "trivela.com.br"),
]
# Para o bloco "fora do futebol": páginas de seção do ge (garantem o esporte, com URL real).
GE_OUTROS = {"Basquete": "basquete", "Fórmula 1": "f1", "Tênis": "tenis", "Vôlei": "volei",
             "Lutas": "combate", "Atletismo": "atletismo", "Olímpicos": "olimpiadas"}


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


def coletar_largo(alvo):
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
    for nome, sec in GE_OUTROS.items():
        tarefas.append((f"ge {nome}", lambda sec=sec: [{"veiculo": "ge", "titulo": i["titulo"], "url": i["url"],
                        "data": i["quando"][:10], "naoFutebol": True} for i in ge_pagina(f"https://ge.globo.com/{sec}/")]))
    itens, falhas = [], []
    with cf.ThreadPoolExecutor(PARALELO) as ex:
        futuros = {ex.submit(f): n for n, f in tarefas}
        for fut in cf.as_completed(futuros):
            try:
                itens.extend(fut.result() or [])
            except Exception as erro:
                falhas.append(f"{futuros[fut]}: {str(erro)[:80]}")
    # filtra pela data-alvo (BRT) e tira repetidos (mesmo título normalizado no mesmo veículo)
    vistos, unicos = set(), []
    for i in itens:
        if i.get("data") != alvo:
            continue
        chave = (i["veiculo"], norm(i["titulo"]))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(i)
    return unicos, falhas


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


SISTEMA = """Você é editor-chefe de esporte. Seu olhar favorece BASTIDORES, DINHEIRO e TRIBUNAL DO FUTEBOL (gestão, folha, dívida, justiça esportiva, arbitragem, acordos), nessa ordem de prioridade, sem ignorar o que for grande em campo. Escreve de forma executiva e direta.

Seu trabalho: ler o dossiê do noticiário de ontem e montar CARDS DE PAUTA — cada card é um tema/evento quente, com o que extrair, perguntas para apurar e pautas do entorno. Pense para a frente: o que a redação faz com isso hoje e nos próximos dias.

Regras invioláveis:
- Use SOMENTE itens do dossiê. Nunca invente matéria, veículo ou URL. Em "fontes", copie veículo e URL EXATOS do dossiê. Um card precisa de pelo menos uma fonte real.
- As perguntas e os ângulos são seus (editoriais), mas só podem partir do que as manchetes afirmam. Não afirme fato que a manchete não traz; se é hipótese, escreva como pergunta.
- Descarte: retrospectiva, balanço, lista evergreen, "tudo sobre", "saiba quem é", "onde assistir", calendário, regulamento.
- Cota é teto, não piso. Prefira 4 cards fortes a 8 fracos. Dia fraco é resposta válida.

Responda APENAS com um JSON válido, sem texto antes ou depois, neste formato exato:
{
 "cards": [
   {
     "beat": "Dinheiro | Tribunal | Bastidor | Clube | Seleção",
     "tipo": "Gancho de ontem | Evento próximo",
     "titulo": "frase-título da pauta",
     "resumo": "uma linha de contexto",
     "oQueTirar": ["pontos a explorar; pode terminar com (Veículo)"],
     "perguntas": ["perguntas concretas para apurar"],
     "entorno": ["pautas de curto/médio prazo que o tema abre"],
     "fontes": [{"veiculo": "", "titulo": "", "url": ""}]
   }
 ],
 "foraDoFutebol": [{"titulo": "", "veiculo": "", "url": "", "angulo": ""}],
 "rotaDaSemana": [{"dia": "ex: Qui 13/11", "foco": "o que acompanhar"}]
}
Faça de 4 a 7 cards, priorizando dinheiro/tribunal/bastidor. foraDoFutebol: até 4, só outros esportes. rotaDaSemana: até 5 linhas do que já dá para prever pelas manchetes (jogos, julgamentos, eleições, prazos). Textos curtos e concretos."""


def chamar_claude(texto):
    from anthropic import Anthropic
    cliente = Anthropic()
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if MODELO in FALLBACK_OK else {}
    with cliente.beta.messages.stream(
        model=MODELO, max_tokens=32000,
        thinking={"type": "adaptive"},
        output_config={"effort": ESFORCO},
        system=SISTEMA,
        messages=[{"role": "user", "content": f"Dossiê de ontem:\n\n{texto}\n\nMonte os cards de pauta no formato JSON pedido."}],
        **extra,
    ) as fluxo:
        msg = fluxo.get_final_message()
    bruto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    return json.loads(bruto[inicio:fim + 1])


def grounded(relatorio, urls_validas):
    """Mantém só fontes com URL real do dossiê; descarta card que ficar sem fonte (anti-invenção)."""
    cards = []
    for card in relatorio.get("cards", []):
        card["fontes"] = [f for f in card.get("fontes", []) if f.get("url") in urls_validas]
        if card["fontes"]:
            cards.append(card)
    relatorio["cards"] = cards
    relatorio["foraDoFutebol"] = [x for x in relatorio.get("foraDoFutebol", []) if x.get("url") in urls_validas]
    return relatorio


def main():
    teste = "--teste" in sys.argv
    hoje_flag = "--hoje" in sys.argv          # janela = hoje até agora (parcial), em vez de ontem fechado
    dias = 0 if hoje_flag else 1
    alvo = (agora().astimezone(BRT) - timedelta(days=dias)).date().isoformat()
    itens, falhas = coletar_largo(alvo)
    texto = dossie(itens)
    if teste:
        print(texto)
        print(f"\n== {len(itens)} itens ({alvo}{' parcial' if hoje_flag else ''}) | falhas: {len(falhas)} ==")
        for f in falhas[:8]:
            print("  -", f)
        return
    relatorio = chamar_claude(texto)
    relatorio = grounded(relatorio, {i["url"] for i in itens if i.get("url")})
    relatorio["geradoEm"] = agora().astimezone(BRT).isoformat(timespec="minutes")
    relatorio["janela"] = alvo
    relatorio["parcial"] = hoje_flag
    relatorio["totalItens"] = len(itens)
    relatorio["fontesForaDoAlcance"] = ["CazéTV (canal de YouTube, sem portal de texto)", "blogs independentes (não enumeráveis)"]
    json.dump(cifrar(json.dumps(relatorio, ensure_ascii=False)), open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editorial.json"), "w", encoding="utf-8"))
    print(f"editorial.json gravado | janela {alvo}{' parcial' if hoje_flag else ''} | {len(itens)} itens | modelo {MODELO}/{ESFORCO} | falhas {len(falhas)}")


if __name__ == "__main__":
    main()

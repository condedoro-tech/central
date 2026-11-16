"""Sondagem (Fase 2 do guia 'Em alta hoje'): testa GDELT e Google Notícias RSS when:6h no runner do Actions.
Não grava nada, só imprime no log. Fontes gratuitas, sem chave. Uso: python ferramentas/sonda.py"""
import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "RadarUOLEsporte/sonda (contato: redacao)"}
ENTIDADES = ['"Corinthians"', '"Flamengo"', '"Palmeiras"', '"São Paulo FC"', '"Santos FC"',
             '"Seleção Brasileira"', '"Ancelotti"', '"Neymar"', '"Vini Jr"']


def baixar(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore"), r.headers.get("Content-Type", "")


def gdelt(termo):
    q = f'({termo} sourcecountry:BR sourcelang:portuguese)'
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": q, "mode": "timelinevol", "timespan": "7d", "format": "json"})
    try:
        corpo, ctype = baixar(url)
    except Exception as e:
        return f"ERRO rede: {str(e)[:60]}"
    if "json" not in ctype.lower() and not corpo.lstrip().startswith("{"):
        return f"NÃO-JSON (ctype={ctype}): {corpo[:80]!r}"
    try:
        d = json.loads(corpo)
    except Exception:
        return f"JSON inválido: {corpo[:80]!r}"
    series = d.get("timeline") or []
    if not series:
        return f"sem timeline (chaves: {list(d.keys())})"
    pontos = series[0].get("data", [])
    vals = [p.get("value", 0) for p in pontos]
    if len(vals) < 7:
        return f"poucos pontos ({len(vals)})"
    ult = vals[-4:]            # ~últimas 24h (pontos costumam ser de 15min/hora; aproximação)
    base = vals[:-4] or [0]
    import statistics
    m_ult = statistics.mean(ult)
    m_base = max(statistics.mean(base), 0.01)
    return f"pontos={len(vals)} | média24h~{m_ult:.3f} base~{m_base:.3f} | razão~{m_ult/m_base:.2f}"


def gnews(termo):
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(termo + ' when:6h')}"
           "&hl=pt-BR&gl=BR&ceid=BR:pt-419")
    try:
        xml, _ = baixar(url)
    except Exception as e:
        return f"ERRO rede: {str(e)[:60]}"
    import re
    itens = re.findall(r"<item>(.*?)</item>", xml, re.S)
    veiculos = {}
    for it in itens:
        m = re.search(r'<source url="[^"]*">(.*?)</source>', it, re.S)
        v = (m.group(1) if m else "?").strip()
        veiculos[v] = veiculos.get(v, 0) + 1
    return f"itens={len(itens)} | veículos distintos={len(veiculos)} | top={list(veiculos)[:6]}"


def main():
    print("=== GDELT DOC 2.0 (timelinevol, 7d, BR/pt) ===")
    for e in ENTIDADES:
        print(f"  {e:24} -> {gdelt(e)}")
        time.sleep(5)       # ritmo do GDELT
    print("\n=== Google Notícias RSS (when:6h, <source> = veículo) ===")
    for e in ENTIDADES:
        print(f"  {e:24} -> {gnews(e)}")
        time.sleep(1.5)
    print("\n== sondagem concluída ==")


if __name__ == "__main__":
    main()

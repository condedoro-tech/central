# Central

Painel privado de esporte, atrás de senha e cifrado (AES-256-GCM); nada legível é publicado. Tem duas camadas:

- **Leitura do dia** (1x/dia): uma coleta larga do noticiário de ontem (ge, UOL, ESPN, Lance!, Terra, R7, Goal, TNT Sports,
  Trivela) vira um dossiê com veículo + data + URL de cada matéria, e o Claude monta **cards de pauta** (beat, o que tirar,
  perguntas para levar, entorno, fontes) + "fora do futebol" + "rota da semana" — **só sobre os itens coletados** (cada card
  carrega as fontes reais; card sem fonte é descartado). O olhar prioriza bastidores, dinheiro e tribunal do futebol.
- **Em alta agora** (a cada 2h): buscas de Esportes em alta no Google Trends cruzadas com a cobertura de UOL/ge/ESPN.

Fora do alcance: redes sociais (X/Instagram/TikTok/YouTube) não são monitoradas; CazéTV é canal de YouTube, sem portal de
texto; "blogs independentes" é genérico demais para coletar. A página diz isso em vez de fingir.

## Arquivos

| Arquivo | O que é |
|---|---|
| `ferramentas/tendencias.py` | Coletor "ao vivo" (Trends + UOL/ge/ESPN). Grava `tendencias.json` cifrado, a cada 2h. |
| `ferramentas/editor.py` | Leitura do dia: coleta larga + Claude. Grava `editorial.json` cifrado, 1x/dia. |
| `ferramentas/montar.py` + `modelo-senha.html` | Montam o `index.html` (tela de senha) a partir de `conteudo/central.html`. |
| `conteudo/central.html` | A página interna, em blocos. |
| `.github/workflows/` | `tendencias.yml` (2h) e `editorial.yml` (1x/dia). |

A chave vem da senha por PBKDF2-SHA256 (600000 iterações) com um sal público no `index.html`. O mesmo sal cifra os dois
JSON, por isso página e dados abrem com a mesma senha.

## Configuração

1. **Segredos no GitHub** (Settings → Secrets and variables → Actions):
   - `CENTRAL_SENHA`: a senha da Central (a mesma do `montar.py`).
   - `ANTHROPIC_API_KEY`: chave da sua conta Anthropic (console.anthropic.com). Só para a leitura do dia.
2. **Montar a página** com a sua senha (uma vez) e commitar:
   ```bash
   pip install cryptography
   CENTRAL_SENHA='sua-senha' python ferramentas/montar.py
   git add index.html && git commit -m "index montado" && git push
   ```
3. **Pages** (repositório público): Settings → Pages → branch `main`. Fica em `https://SEU-USUARIO.github.io/central/`.
4. Dispare os dois workflows uma vez (aba Actions → Run workflow) para gerar os primeiros `tendencias.json` e `editorial.json`.

## Rodar na sua máquina

```bash
pip install playwright cryptography anthropic
python -m playwright install chromium
```

```bash
python ferramentas/tendencias.py --teste                 # Trends + cobertura, só imprime
python ferramentas/editor.py --teste                     # monta o dossiê de ontem, sem IA, sem senha, de graça
CENTRAL_SENHA=... ANTHROPIC_API_KEY=... python ferramentas/editor.py   # gera a leitura do dia cifrada
```

Use `--teste` no `editor.py` primeiro: ele mostra o dossiê que iria para a IA, sem gastar nada. Só depois rode a versão real.

## Custo e modelo

A leitura do dia é a única parte que custa (chamada de IA). Padrão: `claude-opus-5-5`, **~US$ 0,30–0,60/dia**. Para gastar
~metade, troque uma linha no topo do `ferramentas/editor.py`:

```python
MODELO = "claude-sonnet-5-5"   # era "claude-opus-5-5"
```

`ESFORCO` (low/medium/high/...) no mesmo lugar regula profundidade × custo. O coletor "ao vivo" e a página são de graça.

## Trocar a senha

```bash
CENTRAL_SENHA='nova-senha' python ferramentas/montar.py --sal-novo
```
Gera sal novo e **invalida os JSON antigos** — atualize o segredo `CENTRAL_SENHA` e rode os dois workflows de novo.

## Limites de chamada

Os coletores tentam cada fonte no máximo **2 vezes** e desistem (sem insistir em 403/404/429), com pausa entre chamadas e
no máximo 4 simultâneas. Fonte que falha entra na lista de falhas, sem derrubar o resto.

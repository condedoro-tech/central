# Central

Painel privado de tendências de futebol. Junta as buscas em alta de Esportes no Google Trends (4h e 24h) com o
noticiário recente de UOL, ge e ESPN (incluindo as páginas dos 20 clubes da Série A), cruza um com o outro e mostra
onde há busca sem cobertura. Tudo fica atrás de uma senha e o conteúdo é cifrado (AES-256-GCM); nada legível é publicado.

## Como funciona

- `ferramentas/tendencias.py` coleta os dados e grava `tendencias.json` **cifrado**. Roda sozinho no GitHub Actions
  (`.github/workflows/tendencias.yml`), de 2 em 2 horas, sem depender do seu computador.
- `index.html` é a tela de senha. Ela decifra, no seu navegador, a página interna (`conteudo/central.html`), que por sua
  vez busca e decifra o `tendencias.json`. A senha nunca sai do navegador; o servidor só guarda bytes cifrados.
- `ferramentas/montar.py` monta o `index.html` a partir de `conteudo/central.html` + a tela de senha.

A chave vem da senha por PBKDF2-SHA256 (600000 iterações) com um sal público guardado no `index.html`. O mesmo sal é
usado para cifrar o `tendencias.json`, por isso a página e os dados abrem com a mesma senha.

## Primeira configuração

1. Defina a sua senha e monte a página:
   ```bash
   pip install cryptography
   CENTRAL_SENHA='sua-senha-aqui' python ferramentas/montar.py
   ```
   Isso gera um sal novo na primeira vez e grava o `index.html`. Faça commit dele.
2. No GitHub: Settings → Secrets and variables → Actions → New secret, nome `CENTRAL_SENHA`, com a **mesma** senha.
3. Ative o GitHub Pages (Settings → Pages → branch `main`). A tela de senha fica em `https://SEU-USUARIO.github.io/central/`.
4. Dispare o workflow uma vez (aba Actions → Tendencias de hoje → Run workflow) para gerar o primeiro `tendencias.json`.

## Rodar na sua máquina

```bash
pip install playwright cryptography
python -m playwright install chromium
python ferramentas/tendencias.py --teste      # só imprime, não cifra nem grava
CENTRAL_SENHA='sua-senha' python ferramentas/tendencias.py   # grava tendencias.json cifrado
```

## Trocar a senha

```bash
CENTRAL_SENHA='nova-senha' python ferramentas/montar.py --sal-novo
```
Gera um sal novo, remonta o `index.html` e **invalida o tendencias.json antigo** — rode o coletor de novo (e atualize o
segredo `CENTRAL_SENHA` no GitHub) para regravá-lo com a nova chave.

## Limites de chamada

O coletor tenta cada fonte no máximo **2 vezes** e desiste (sem insistir em 403/404/429), com pausa entre chamadas e
no máximo 4 chamadas simultâneas. Fonte que falha entra em `falhas`, sem derrubar o resto.

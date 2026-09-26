# Spike: legenda direto do player (Netflix/YouTube)

Pergunta que este teste responde: **a legenda continua chegando na hora certa
quando a aba não está visível** (Firefox atrás do VS Code, em outro workspace,
minimizado, outra aba ativa)? Se sim, o overlay pode mostrar a legenda do
próprio serviço com atraso ~zero, sem Whisper.

Peças:
- `extension/` — extensão do Firefox. `probe.js` observa o elemento de legenda
  na página e anota cada troca com o tempo do vídeo; `hook.js` intercepta o
  arquivo de legenda que o player baixa (TTML na Netflix, json3 no YouTube).
- `server.py` — recebe os eventos em `127.0.0.1:8765`, grava em JSONL e mostra
  ao vivo. Texto digitado no terminal vira **marcador de cenário**.
- `analyze.py` — por cenário: atraso da legenda na tela vs. o horário do arquivo,
  legendas perdidas, e se os timers da aba foram estrangulados.

## Como rodar

1. Terminal 1 (deixe aberto, é nele que você digita os marcadores):
   ```sh
   .venv/bin/python spikes/subtitle_probe/server.py --out probe.jsonl
   ```
2. Firefox → `about:debugging#/runtime/this-firefox` → **Carregar extensão
   temporária…** → escolha `spikes/subtitle_probe/extension/manifest.json`.
   (Vale até fechar o Firefox.) Em `about:addons` → Live Caption Probe →
   **Permissões**, confirme que netflix.com, youtube.com e 127.0.0.1 estão ligados.

   Sem cliques: com o Firefox fechado, ponha no `user.js` do perfil
   `devtools.debugger.remote-enabled`, `devtools.chrome.enabled` = true e
   `devtools.debugger.prompt-connection` = false (as duas primeiras são exigidas
   pelo `--start-debugger-server`), e rode
   ```sh
   firefox --start-debugger-server 6000 &
   .venv/bin/python spikes/subtitle_probe/load_extension.py --port 6000
   ```
   Depois do teste, volte esses prefs ao padrão (o `prefs.js` guarda o valor).
3. Abra um episódio na Netflix com legenda **PT-BR ligada**. Se a aba já estava
   aberta antes de carregar a extensão, **recarregue a página (F5)**: o arquivo de
   legenda só é interceptado se a extensão já estiver lá quando o player baixa.
   No terminal deve aparecer `captured ttml track` e as falas com `vt=`.
   Se o ícone da extensão mostrar `ERR`, o servidor não está rodando.

## Roteiro (~2 min por cenário, sem pausar nem pular o vídeo)

Digite o marcador no terminal + Enter, **depois** faça a ação:

| Marcador | Ação |
|---|---|
| `visivel` | assistir normal, Firefox em foco |
| `atras do vscode` | clicar no VS Code maximizado, cobrindo o Firefox |
| `outro workspace` | levar o Firefox para outro workspace (ou mudar você de workspace) |
| `minimizado` | minimizar o Firefox (Super+H) |
| `outra aba` | trocar para outra aba na mesma janela do Firefox |
| `visivel de novo` | voltar para a aba da Netflix |

Ctrl+C no servidor e:

```sh
.venv/bin/python spikes/subtitle_probe/analyze.py probe.jsonl
```

Me mande a saída do `analyze` (o `probe.jsonl` tem o texto das legendas, não
precisa commitar). Opcional: repetir com um vídeo do YouTube com legenda.

## Como ler o resultado

- **lag vs track**: quanto a legenda apareceu na tela depois do horário do
  arquivo (em tempo de vídeo). Visível deve dar ~0–200 ms; se um cenário der
  segundos, a Netflix atrasa a renderização naquele estado.
- **missed subtitles**: falas do arquivo que nunca apareceram na tela.
- **timer heartbeat**: gaps > 1 s = timers da aba estrangulados (afeta um
  sincronizador próprio, não o observador de DOM).
- `subtitle tracks captured: 0` = interceptação falhou (o player baixa a
  legenda por outro caminho). Não invalida o teste: o fluxo na tela ainda é medido.
- `no subtitle element on page` no terminal = legenda desligada, ou a Netflix
  mudou o nome das classes (`.player-timedtext`).

## Resultado (2026-09-26, Netflix, House, legenda PT-BR, Firefox 156 snap)

| Cenário | na tela / arquivo | perdidas | lag p50 | lag p95 | heartbeat máx |
|---|---|---|---|---|---|
| visível | 50 / 50 | 1 | 13 ms | 410 ms | 1,0 s |
| atrás do VS Code | 22 / 21 | 0 | 10 ms | 870 ms | 1,0 s |
| outro workspace (hidden) | 26 / 26 | 0 | 10 ms | 784 ms | 1,0 s |
| minimizado (hidden) | 24 / 23 | 0 | 26 ms | 665 ms | 1,0 s |
| outra aba (hidden) | 23 / 22 | 0 | 30 ms | 709 ms | 1,0 s |
| visível de novo | 47 / 44 | 0 | 18 ms | 374 ms | 1,0 s |

- **Visibilidade não afeta nada.** Escondida, minimizada ou em outra aba, a
  legenda chega no DOM igual a quando está visível; entrega ao receptor p95 3 ms.
  Coberta por outra janela, a aba continua `visible` (Wayland não informa oclusão).
- **O renderizador da Netflix é que é impreciso, em qualquer estado:** o *fim*
  das falas sai na hora (158/164 < 100 ms), mas ~12% dos *inícios* saem 0,4–1 s
  atrasados, quase sempre quando a fala começa colada na anterior (vão de 83 ms =
  2 quadros). Uma fala de 1 s nesse padrão nunca foi desenhada.
- **Interceptação funciona:** a Netflix baixa o TTML (IMSC 1.1, `tickRate`
  10 000 000) do **episódio inteiro de uma vez** (562 falas) ao abrir o player, e
  também os das prévias em autoplay na `/browse` — o analisador escolhe a trilha
  ativa casando com o texto da tela. Seletores `.player-timedtext*` corretos.
- Heartbeat de 1 s não mostrou estrangulamento, mas também não detectaria o
  limite de 1 s que o Firefox aplica a timers de abas em segundo plano (e abas
  tocando áudio são isentas) — não medido com timer mais curto.

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

## Teste 2: intervalos comerciais (plano Netflix com anúncios)

A legenda sincronizada pelo arquivo depende de o relógio do vídeo ser o relógio
do **conteúdo**. No plano com anúncios não sabemos o que a Netflix faz durante um
intervalo: o `<video>` pode continuar contando (e aí a legenda fica adiantada
pela duração do anúncio), o anúncio pode tocar em outro `<video>`, ou o player
pode trocar de fonte. E o overlay não deve legendar o anúncio. Este teste mede
isso. A extensão agora também registra:

- todos os elementos `<video>` a cada segundo (tempo, duração, fonte);
- marcadores `data-uia` da interface que aparecem e somem (é como o "Anúncio ·
  0:30" deve aparecer) com o texto de cada um;
- a API interna do player da Netflix: lista de métodos e, a cada segundo, os
  valores dos métodos de consulta ligados a tempo e anúncio.

Roteiro (recarregue a extensão e a página antes; mesmo servidor de antes, com
outro arquivo de log):

```sh
.venv/bin/python spikes/subtitle_probe/server.py --out ads.jsonl
```

| Marcador | Ação |
|---|---|
| `pre-roll` | abrir um episódio **do começo**; deixar tocar o anúncio inicial (se houver) e ~1 min do episódio |
| `mid-roll` | na barra de progresso, pular para ~20 s **antes** de uma marquinha de intervalo; deixar o anúncio inteiro tocar e mais ~1 min depois |
| `pausa no anuncio` | (se aparecer outro intervalo) pausar uns segundos durante o anúncio e retomar |

Sem mexer no vídeo fora dessas ações. Depois:

```sh
.venv/bin/python spikes/subtitle_probe/analyze.py ads.jsonl --timeline
```

`--timeline` imprime só as transições. O que procurar em volta do anúncio:

- `ui + ...` com texto de anúncio = como detectar o intervalo pela tela;
- `video#0.currentTime running` durante o anúncio e depois
  `video time - subtitle file time = +NN s` = o `<video>` conta o anúncio (o app
  vai precisar descontar); se continuar `+0.0x s`, não conta;
- `2 <video> element(s)` / `video #1 loadedmetadata` = anúncio em elemento separado;
- `api.<algo> frozen` durante o anúncio = a API tem o relógio do conteúdo;
- `player API: ... ad-related: ...` = métodos com nome de anúncio para investigar;
- `api currentTime - segmentTime = +NN s` = quanto o `<video>` está adiantado em
  relação ao conteúdo (a soma dos anúncios já tocados).

Sem ninguém no teclado: `drive.py` faz o papel do dono pelo mesmo RDP do
`load_extension.py` — abre a aba, avalia JS na página (onde fica a API do
player) e manda os marcadores por POST para o `server.py`.

## Resultado do teste 2 (2026-09-26, Netflix com anúncios, House T5, PT-BR, Firefox 156 snap)

Rodado sem ninguém mexendo (`drive.py`), aba visível, janela sem foco.

| Cenário | O que aconteceu |
|---|---|
| mid-roll com seek 20 s antes (E1, intervalo 26:57) | intervalo hidratado no seek, **vazio**; conteúdo seguiu direto |
| mid-roll sem seek (E1, intervalo 33:39, ~6 min de reprodução normal) | **1 anúncio de 32 s** |
| pre-roll (E2 aberto do começo, nunca assistido) | intervalo em 0 ms vazio, sem anúncio (4 min depois do anterior) |
| mid-roll com seek 90 s antes (E2, 17:07) | hidratado ~59 s antes, **vazio** |
| pausa no anúncio (E2, 26:03 e 34:02, ~20 min sem seek) | os dois intervalos hidratados ~59 s antes, **vazios** — não houve anúncio para pausar (não medido) |

Um único anúncio servido em ~35 min de reprodução, com 6 intervalos passados
(contando o pre-roll).
No resumo por cenário (`analyze.py` sem `--timeline`), o efeito aparece do
outro lado: em "mid-roll 2" 37 das 98 falas na tela não casam com o arquivo no
tempo do `<video>` — são todas as de depois do anúncio, 32 s deslocadas.

O que o anúncio de 32 s mostrou (`analyze.py --timeline`, trecho):

```
13:20:46  ui + ads-info-container  'Anúncio31'
13:20:46  ui + video-title  'Dr. House volta após os anúncios'
13:20:46  api.ad.presenting = True
13:20:46  api.ad.canSeek = False
13:20:47  api.getSegmentTime frozen (2019267.00 -> 2019267.00 in 1.0 s)
13:21:18  api currentTime - segmentTime = +32.03 s
13:21:18  api.getSegmentTime running (2019267.00 -> 2019643.00 in 1.0 s)
13:21:18  api.ad.presenting = False
13:21:18  ui - ads-info-container
13:21:28  video time - subtitle file time = +32.03 s  ('Não espere que as coisas')
```

- **O `<video>` conta o anúncio.** O anúncio é emendado na mesma timeline MSE do
  mesmo `<video>` (continua 1 elemento, mesma fonte `blob:`), `currentTime`
  segue correndo e depois dele a legenda do arquivo ficaria **32,03 s
  adiantada** — exatamente a duração do anúncio. `getDuration()` não muda.
- **Nenhum evento de mídia nas bordas** (`play`/`pause`/`seeked`/`emptied`/
  `durationchange` — nada). Âncoras disparadas só por evento de mídia não veem o
  intervalo.
- **A API do player tem o relógio do conteúdo:** `getSegmentTime()` congela
  exatamente no `locationMs` do intervalo durante o anúncio e retoma em tempo de
  conteúdo; `getCurrentTime()` é o relógio do `<video>` (conta anúncio). A
  diferença entre os dois é o desconto, e volta a 0 quando a página recarrega
  (a nova timeline começa já na posição do conteúdo).
- **Sinal de anúncio na tela:** `getAdManager().adPresenting.value` (um
  observável — tem `addListener`) vira `true`/`false` no mesmo segundo que a
  interface mostra/tira `data-uia="ads-info-container"` ("Anúncio" + contagem
  regressiva em `ads-info-time`). `canSeek()` fica `false` durante o anúncio.
  A Netflix não desenha legenda nenhuma durante o anúncio.
- **A grade de intervalos vem de antemão:** `getAdManager().getAds()` lista os
  intervalos do episódio em tempo de conteúdo (`locationMs`, também em ticks de
  1/24000 s) desde o início; cada um é *hidratado* (recebe os anúncios) ~60 s
  antes, ou na hora de um seek, e pode vir vazio. A barra de progresso tem
  `data-uia="ad-markers"`.
- **Quando a Netflix não serve anúncio:** 5 dos 6 intervalos vieram vazios —
  depois de seek, com anúncio recente, e também dois com ~20 min de reprodução
  contínua; o único servido foi depois de ~6 min de reprodução contínua. Não dá para separar "regra de seek" de "limite de
  frequência" com uma conta só — e para o app tanto faz: intervalo vazio não
  mexe em nenhum relógio (`currentTime - segmentTime` fica 0).
- Autoplay: navegar pelo RDP não conta como gesto do usuário e o Firefox mostra
  `player-blocked-play`; o `drive.py` não contorna isso sozinho — neste teste foi
  dada a permissão `autoplay-media` só para a sessão (`EXPIRE_SESSION`).

### Como o app deve detectar e descontar os anúncios

1. **Relógio do conteúdo na extensão, não no app.** Cada âncora leva
   `vt = video.currentTime − (getCurrentTime() − getSegmentTime()) / 1000`: usa a
   precisão do `<video>` e desconta os anúncios já tocados. O desconto é
   recalculado em **toda** âncora (nunca guardado): ele zera ao recarregar a
   página, ao trocar de episódio e, se a Netflix mudar isso, num seek.
2. **Intervalo = `adPresenting.value`**, assinado com `addListener` (evento, não
   timer — sem estrangulamento em aba escondida) e conferido em cada âncora.
   Enquanto for `true`, a âncora vai com `ad: true` e o `vt` congelado no
   `getSegmentTime()`; o app esconde a legenda e para o relógio. No fim do
   anúncio, uma âncora normal (já com o desconto novo) retoma tudo.
3. **Reserva, se a API mudar:** `data-uia="ads-info-container"` presente na
   página (MutationObserver) = anúncio na tela; o desconto então vem da rede de
   segurança do plano — o texto da tela casado com o arquivo mede o deslocamento
   (aqui deu +32,03 s, exato).
4. Não usar a grade `getAds()` para *prever* anúncio: intervalo vazio é comum.

Fica para o critério da fase 4 (não houve anúncio para medir): pausar no meio do
anúncio — o esperado é `adPresenting` continuar `true` e `segmentTime` parado.

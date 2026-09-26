# CLAUDE.md

Legendas em tempo real de **um** app/aba (a legenda do próprio player, ou Whisper
quando não há legenda), exibidas num overlay flutuante. Projeto pessoal, só
Linux + PipeWire. Plano completo em `docs/PLAN.md`.

## Como trabalhamos neste projeto

- Fases incrementais e testáveis (ver `docs/PLAN.md`); só avançar quando o critério
  de "funcionando" da fase atual foi verificado **na máquina real**, não só no pytest.
- Latência percebida (1–3 s) vale mais que transcrição perfeita. Corte/repetição de
  palavras no streaming é ajuste iterativo (fase 5), não bug a "resolver".
- Sem APIs pagas na v1. Tradução offline (Argos Translate).
- O Claude escreve todo o código (o dono não quer TODOs para ele implementar);
  explicar as decisões de design em vez de delegá-las.
- Conversa em português; código, identificadores e mensagens de commit em inglês.

## Comandos

```sh
.venv/bin/pip install -e '.[dev,gpu]'      # setup (venv com Python 3.14 do sistema)
.venv/bin/pytest                           # testes
.venv/bin/python -m live_caption list      # apps/abas tocando áudio
.venv/bin/python -m live_caption record [query] [--seconds N] [--out f.wav]
                                           # sem query: menu interativo de abas
.venv/bin/python -m live_caption transcribe [query] [--file f.wav] [--lang en] [--model small]
.venv/bin/python -m live_caption bench f.wav   # tempo por passada, por modelo
.venv/bin/python spikes/subtitle_probe/server.py --out probe.jsonl   # spike fase 3
.venv/bin/python spikes/subtitle_probe/analyze.py probe.jsonl
.venv/bin/python spikes/subtitle_probe/load_extension.py   # extensão temporária via RDP
                                           # (--dir extension para a definitiva)
.venv/bin/python spikes/subtitle_probe/drive.py eval netflix.com/watch 'JS'  # JS na aba
                                           # (também: open URL, mark 'nota')
```

## Arquitetura (até agora)

- `src/live_caption/audio/streams.py` — lê `pw-dump` (JSON) e extrai nós
  `Stream/Output/Audio`. `media.name` = título da aba no Firefox.
- `src/live_caption/audio/capture.py` — `StreamCapture` roda `pw-record` apontado
  para o **serial** do stream; PipeWire já entrega 16 kHz mono s16 (formato do
  Whisper). Levanta `StreamGone` quando o app some.
- `src/live_caption/audio/sources.py` — `ChunkPump` (thread que drena a captura
  enquanto o Whisper roda; o pipe de 64 KiB lota em ~2 s) e `file_chunks` (replay
  de arquivo em tempo real, para testes repetíveis).
- `src/live_caption/asr/agreement.py` — LocalAgreement-2: palavra só é confirmada
  quando duas passadas seguidas concordam; dedupe de n-gramas na fronteira.
- `src/live_caption/asr/streaming.py` — `StreamingTranscriber`: buffer deslizante
  re-transcrito a cada passo, cortado no fim da última frase confirmada.
- `src/live_caption/asr/whisper_engine.py` — faster-whisper com portão de VAD,
  `temperature=0`, idioma travado após detecção confiante.
- `src/live_caption/asr/gpu.py` — pré-carrega cuBLAS/cuDNN do venv via ctypes.
- `src/live_caption/pipeline.py` — chunks → updates com medição de atraso.
- `src/live_caption/cli.py` — subcomandos `list`, `record`, `transcribe`, `bench`.
- `src/live_caption/subs/timedtext.py` — parser de TTML (Netflix, tempos em ticks),
  WebVTT e json3 (YouTube) → lista de `Cue(start, end, text)`; `normalize` para casar
  texto da tela com o arquivo.
- `extension/` — extensão definitiva (fase 4). `page.js` roda no mundo da página:
  intercepta o TTML, lê a API interna do player e manda âncoras em **tempo de
  conteúdo** (anúncio descontado, `ad`/`paused` durante o intervalo) + texto da tela;
  `content.js` só repassa; `background.js` faz POST em `127.0.0.1:8765/event`.
- `spikes/subtitle_probe/` — extensão Firefox (MV3) + receptor HTTP local + analisador
  para medir se a legenda do player chega em dia com a aba escondida (teste 1) e o
  que acontece num intervalo comercial (teste 2, `analyze.py --timeline`).

## Fatos do ambiente que já custaram descoberta

- Hardware: i7-12650H, RTX 3050 Laptop **4 GiB VRAM**, GNOME **Wayland**, PipeWire 1.6.2.
- Modelo recomendado: `small` `int8_float16` na GPU; upgrade: `large-v3-turbo`.
- cuBLAS/cuDNN não estão no sistema → vêm do extra `gpu` (pip) e são pré-carregados
  por `asr/gpu.py` (LD_LIBRARY_PATH só vale se definido antes do processo iniciar).
- Hugging Face limita a API por IP (429) nesta rede → modelos baixados via `/resolve/`
  para `~/.cache/live-caption/models/<nome>` (ver README); o engine usa essa pasta se existir.
- Notebook costuma estar **na bateria e em `power-saver`**: GPU fica com clock baixo.
  Números de latência dependem disso — anotar a condição ao medir.
- Whisper recita o `initial_prompt` sobre silêncio; *temperature fallback* causa
  passadas de 5–10 s. Ambos já tratados no engine — não remover sem medir.
- Captar o **nó do stream** do app, não o monitor do sink (monitor mistura todos os apps).
- Alvo por `object.serial`, nunca por `id` (ids são reciclados).
- `node.dont-reconnect`/`node.dont-fallback` no `pw-record` são essenciais: sem eles,
  quando o app some, o WirePlumber religa a captura no **microfone**.
- Firefox **destrói o stream ao pausar** e cria outro (serial novo) ao dar play.
- Captura é pós-volume do app: aba mutada = silêncio.
- Chrome junta todas as abas num stream só; isolamento por aba só no Firefox.
- Overlay no Wayland: usar PySide6 com `QT_QPA_PLATFORM=xcb` para "sempre no topo" funcionar.
- Ao testar com `pw-play` em background, matar pelo PID — `pkill -f` casa com o próprio shell
  (vale para `pgrep -f` também; usar `pgrep -x firefox`).
- Firefox é **snap** (perfil em `~/snap/firefox/common/.mozilla/firefox/eq6j1hce.default`,
  `/tmp` privado). `--start-debugger-server` só abre a porta com
  `devtools.debugger.remote-enabled` **e** `devtools.chrome.enabled`. Não usar WebDriver
  BiDi para carregar a extensão: liga `navigator.webdriver`.
- Netflix baixa o TTML (IMSC 1.1) do **episódio inteiro** ao abrir o player, além dos
  das prévias em autoplay na `/browse`. Seletores `.player-timedtext*` confirmados.
- O renderizador de legenda da Netflix desenha ~12% dos inícios de fala 0,4–1 s
  atrasados (e às vezes pula uma fala curta), com a aba visível ou não.
- Anúncio da Netflix é emendado no **mesmo `<video>`**: `currentTime` conta o anúncio,
  sem evento de mídia nas bordas. Relógio do conteúdo = `getSegmentTime()` da API
  interna do player; anúncio na tela = `getAdManager().adPresenting.value`.
  A Netflix serve anúncio raramente (1 em 6 intervalos no teste) — para testar,
  deixar tocar sem seek.
- Navegar pelo RDP não conta como gesto do usuário: autoplay bloqueado
  (`player-blocked-play`). Para testes, permissão `autoplay-media` com
  `EXPIRE_SESSION` pelo console do processo pai. Reiniciar o Firefox por
  `Services.startup.quit(eRestart | eAttemptQuit)` salva e restaura a sessão;
  SIGTERM não grava o `sessionstore.jsonlz4`.
- O perfil tem **Adblock Plus** ativo; não impediu o anúncio da Netflix.
- O `xml:lang` do TTML da Netflix pode estar errado (arquivo pt-BR dizendo `en`): idioma
  vem de `getTimedTextTrack().bcp47`. A trilha do **próximo** episódio chega ~3 min antes
  do fim; o id dele está em `getState().postPlay.experienceByVideoId[<atual>].items[0].videoId`.
- Os `loadedmetadata`/`play` iniciais da Netflix acontecem com o `<video>` fora do
  documento (listener no `document` não vê); a primeira âncora é um `timeupdate`.

## Onde paramos (atualizar ao fim de cada sessão)

**2026-09-26 (noite) — Fase 4 concluída: extensão definitiva verificada na Netflix.**

`extension/` (protocolo das mensagens e resultados em `extension/README.md`). Verificado
com o receptor do spike + `drive.py`: 1 trilha por episódio, âncoras em
play/pause/seek/1,5×, trilha nova na troca de idioma e no *autoplay* do próximo episódio
(rotulada `prefetch`), e um **pre-roll de ~15 s**: `vt` parado com `ad` durante, falas da
tela casando com o arquivo (mediana 17 ms) depois. Corrigido na verificação: idioma e
episódio da trilha. Não medido: pausar durante um anúncio (nenhum mid-roll servido).

Próximo: **Fase 5** — receptor + `SubtitleClock` + `follow` (ver `docs/PLAN.md`).

Pendência de limpeza: o `user.js` do perfil do Firefox (reset dos prefs de depuração) já
foi aplicado de novo; pode ser apagado.

---

**2026-09-26 (tarde) — Teste 2 do spike (anúncios) rodado; como descontar, decidido.**

Rodado sozinho via `drive.py` (RDP) no perfil do dono, House T5E1–E2: 1 anúncio
de 32 s em 6 intervalos. O `<video>` conta o anúncio (legenda ficaria +32 s
adiantada); `getSegmentTime()` congela no intervalo e é o relógio do conteúdo.
Receita no README do spike ("Como o app deve detectar e descontar") e na fase 4
do `docs/PLAN.md`: `vt = video.currentTime − (getCurrentTime() − getSegmentTime())/1000`
em toda âncora, `ad: true` enquanto `adPresenting`; reserva `data-uia="ads-info-container"`.
Não medido (não houve anúncio): pausa no meio do anúncio — fica no critério da fase 4.


---

**2026-09-26 — Fase 3 (spike) concluída; decidido: arquivo sincronizado.**

Motivo da mudança de rumo: o dono ouve a série numa aba enquanto trabalha no
VS Code e lê só a janelinha always-on-top; ~2 s de atraso do Whisper incomoda, e
as séries já têm legenda PT-BR na Netflix. Continua Python.

Feito: spike `spikes/subtitle_probe/` rodado na máquina real (Netflix, ~11 min,
6 cenários; tabela no README do spike). Aba escondida não atrasa nada; o que
atrapalha "ler da tela" é o renderizador da Netflix (~12% dos inícios de fala
0,4–1 s atrasados, 1 fala pulada). O TTML do episódio inteiro é interceptado.

Decisão do dono: **sincronizar o arquivo pelo relógio do vídeo** — extensão manda
trilha + âncoras de tempo, relógio fica no app Python. Fases reescritas em
`docs/PLAN.md` (4 extensão definitiva → 5 receptor + relógio → 6 overlay →
7 tradução → 8 reservas Whisper/música).

Anúncios: o dono usa o plano da Netflix com anúncios e não quer anúncio legendado
(resolvido no teste 2, entrada acima).

---

**2026-09-25 — Fases 1 e 2 concluídas.**

Feito na fase 2:
- Streaming com faster-whisper `small` na GPU, LocalAgreement-2, buffer deslizante.
- Verificado ponta a ponta via PipeWire (áudio tocado como aba do Firefox):
  atraso na tela p50 0,90 s, confirmação p50 1,95 s / p95 2,79 s (na bateria).
- Corrigidos com base em trace por passada: alucinação do prompt em silêncio,
  passadas lentas por temperature fallback, relógio de latência.

Pendências conhecidas (para a fase 5, não bloqueiam):
- Ocasionalmente uma palavra some na fronteira de confirmação (visto uma vez: "can").
- Whisper às vezes quebra frases com ponto a mais ("Ask not." / "what your country...").
- Dono testou `transcribe` com Netflix real e funcionou (sem números de latência
  registrados). Ainda não testados: idioma ≠ inglês e auto-detecção de idioma.
- Rodar `bench` com o notebook **na tomada** para decidir small vs large-v3-turbo.

(Superado em 2026-09-26: a tradução com Argos virou recurso secundário — ver entrada acima.)

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

## Onde paramos (atualizar ao fim de cada sessão)

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

Próximo: **Fase 4** — extensão definitiva (ver `docs/PLAN.md`), começando pelos
**anúncios**: o dono usa o plano da Netflix com anúncios e não quer anúncio legendado.
Probe estendido (vídeos, marcadores `data-uia`, API interna do player) e
`analyze.py --timeline`, testados só com log sintético — **falta rodar o teste 2**
do README do spike na máquina real e decidir como detectar o intervalo e
descontar o anúncio do relógio.

Pendência de limpeza: o perfil do Firefox tem um `user.js` que volta os prefs de
depuração ao padrão no próximo início; depois disso pode ser apagado.

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

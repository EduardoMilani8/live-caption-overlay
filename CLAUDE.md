# CLAUDE.md

Legendas traduzidas em tempo real do áudio de **um** app/aba, exibidas num overlay
flutuante. Projeto pessoal, só Linux + PipeWire. Plano completo em `docs/PLAN.md`.

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
- Ao testar com `pw-play` em background, matar pelo PID — `pkill -f` casa com o próprio shell.

## Onde paramos (atualizar ao fim de cada sessão)

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
- Não testado ainda com Netflix real, idioma ≠ inglês e auto-detecção de idioma.
- Rodar `bench` com o notebook **na tomada** para decidir small vs large-v3-turbo.

Próximo: **Fase 3** — tradução com Argos Translate, só do texto confirmado (ver `docs/PLAN.md`).

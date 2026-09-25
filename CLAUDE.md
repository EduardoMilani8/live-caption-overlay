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
.venv/bin/pip install -e '.[dev]'          # setup (venv com Python 3.14 do sistema)
.venv/bin/pytest                           # testes
.venv/bin/python -m live_caption list      # apps/abas tocando áudio
.venv/bin/python -m live_caption record [query] [--seconds N] [--out f.wav]
                                           # sem query: menu interativo de abas
```

## Arquitetura (até agora)

- `src/live_caption/audio/streams.py` — lê `pw-dump` (JSON) e extrai nós
  `Stream/Output/Audio`. `media.name` = título da aba no Firefox.
- `src/live_caption/audio/capture.py` — `StreamCapture` roda `pw-record` apontado
  para o **serial** do stream; PipeWire já entrega 16 kHz mono s16 (formato do
  Whisper). Levanta `StreamGone` quando o app some.
- `src/live_caption/cli.py` — subcomandos `list` e `record` (argparse).

## Fatos do ambiente que já custaram descoberta

- Hardware: i7-12650H, RTX 3050 Laptop **4 GiB VRAM**, GNOME **Wayland**, PipeWire 1.6.2.
- Modelo recomendado: `small` `int8_float16` na GPU; upgrade: `large-v3-turbo`.
- cuBLAS/cuDNN não estão no sistema → instalar `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` via pip na fase 2.
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

**2026-09-25 — Fase 1 concluída (falta só confirmar o isolamento).**

Feito:
- Listagem de streams, captura por stream, `StreamGone`, CLI `list`/`record`.
- Menu interativo: sem argumento, `record` pergunta qual aba/app legendar.
- `pick_stream` (atalho `record netflix`): título da aba > nome do app; tocando > pausado.
- Verificado na máquina: dono capturou a aba do Netflix pelo menu (medidor de dBFS
  reagindo); app sumindo encerra a captura sem cair no microfone.

Pendente:
- Confirmar isolamento: Netflix + Spotify juntos → `record --out /tmp/n.wav` →
  `pw-play /tmp/n.wav` só com o Netflix.

Próximo: **Fase 2** — transcrição streaming com faster-whisper (ver `docs/PLAN.md`).
Reconexão quando o título da aba muda (próximo episódio) fica para a fase 5.

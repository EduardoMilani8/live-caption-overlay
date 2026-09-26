# live-caption-overlay — Plano de implementação

## Hardware detectado (2026-09-25)

| Item | Valor |
|---|---|
| CPU | Intel i7-12650H (10 núcleos / 16 threads, AVX2) |
| RAM | 14 GiB |
| GPU | NVIDIA RTX 3050 Laptop, **4 GiB VRAM**, driver 595 (+ Intel UHD integrada) |
| CUDA libs | cuBLAS/cuDNN **não instaladas no sistema** → instalar via pip (fase 2) |
| Áudio | PipeWire 1.6.2 + WirePlumber 0.5.13 (sem `pactl`/`parec`, e não precisamos deles) |
| Sessão | GNOME em **Wayland** (importante para a fase 4) |
| Python | 3.14.4 (ctranslate2, onnxruntime, numpy e PySide6 já têm wheels 3.14) |

## Recomendação de modelo Whisper

O gargalo é a **VRAM de 4 GiB** (parte dela já é usada pelo desktop). No streaming,
o mesmo trecho de áudio é re-transcrito várias vezes (a cada ~1 s), então o que
importa é o tempo **por passada**, não o tempo total.

| Modelo | compute_type | VRAM aprox. | Tempo/passada (janela ~10 s, estimativa) | Veredito |
|---|---|---|---|---|
| `small` | `int8_float16` (GPU) | ~1 GB | ~0,1–0,3 s | **Padrão inicial.** Folga grande para 1–3 s de atraso |
| `large-v3-turbo` | `int8_float16` (GPU) | ~1,5–2 GB | ~0,4–0,8 s | **Upgrade de qualidade** a testar na fase 5 |
| `medium` | — | ~1,5 GB | mais lento que turbo | Descartado: turbo é mais rápido *e* melhor |
| `large-v3` | `float16` | ~3+ GB | ~1,5–2 s | Descartado: risco de OOM e estoura o orçamento de latência |
| `base`/`small` | `int8` (CPU) | — | ~0,5–2 s | Fallback se a GPU não estiver disponível |

Modelos `.en`/`distil-*` são só inglês — descartados porque a origem (Netflix etc.) varia.
Os números são estimativas; a fase 2 mede os reais nesta máquina.

## Decisão de stack da UI

**PySide6 no mesmo processo Python.** Todo o pipeline pesado (faster-whisper, Argos)
é Python; Tauri/egui exigiria um canal IPC (websocket/stdout) e dois toolchains, sem
ganho real — a UI só desenha 2 linhas de texto. Workers rodam em threads e mandam
texto para a UI via *signals* do Qt (thread-safe).

**Pegadinha do Wayland:** no GNOME Wayland, apps nativos **não podem** se colocar
"sempre no topo" (`WindowStaysOnTopHint` é ignorado). Solução: rodar o overlay via
XWayland (`QT_QPA_PLATFORM=xcb`), onde o Mutter respeita o estado "above" e a
transparência. Arrastar funciona com `QWindow.startSystemMove()`.

---

## Fase 1 — Captura de áudio por aplicativo ✅

- **Ferramentas:** `pw-dump` (listar streams, JSON), `pw-record` (captura), `numpy`.
- **Como funciona:** o alvo é o nó `Stream/Output/Audio` do app (não o monitor do
  sink, que mistura todos os apps). PipeWire entrega 16 kHz mono s16 direto.
- **Critério de "funcionando":**
  1. `python -m live_caption list` mostra os apps tocando áudio.
  2. Com Netflix (Firefox) **e** Spotify tocando juntos,
     `python -m live_caption record netflix --seconds 15 --out /tmp/n.wav`
     gera um WAV com **só** o áudio do Netflix (`pw-play /tmp/n.wav`).
  3. Pausar/fechar a aba encerra a captura com mensagem limpa — nunca cai no microfone.
  4. `pytest` verde.
- **Riscos:**
  - **Chrome/Chromium** costuma juntar todas as abas num único stream — isolar por aba
    só é confiável no Firefox (que expõe o título da mídia em `media.name`).
  - Captura é **pós-volume do app**: mutar a aba = legenda muda. (Mute no sistema é ok.)
  - Alguns apps destroem/recriam o stream ao pausar → a fase 5 trata reconexão.

## Fase 2 — Transcrição em streaming ✅

**Medido (2026-09-25, notebook na bateria, `small` int8_float16 na GPU, passo 1 s),
áudio tocado como aba do Firefox e capturado pelo PipeWire:**
passada p50 0,72 s / p95 0,95 s · atraso na tela p50 0,90 s · atraso até confirmar
p50 1,95 s / p95 2,79 s. `bench` na bateria: small ≈ 0,7–1,0 s, large-v3-turbo ≈ 2 s
por passada (turbo não cabe no orçamento na bateria; medir de novo na tomada).

Aprendizados:
- O custo da passada quase não depende do tamanho do buffer (o Whisper sempre
  codifica 30 s); o encoder leva ~0,1 s e o resto é o decoder. O botão de latência é o passo.
- Em silêncio o Whisper **recita o `initial_prompt`** (palavras todas no mesmo timestamp),
  mesmo com `vad_filter` → portão de VAD próprio antes de chamar o modelo.
- *Temperature fallback* gerava passadas de 5–10 s → `temperature=0`.
- A captura precisa abrir **depois** de carregar o modelo (senão áudio velho acumula
  no pipe e o relógio de latência fica errado).


- **Ferramentas:** `faster-whisper` (CTranslate2), Silero VAD (embutido no
  faster-whisper, via `onnxruntime`), `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (pip).
- **Estratégia:** buffer deslizante (até ~10–15 s) re-transcrito a cada passo de ~1 s;
  política *LocalAgreement-2* — só "confirma" o prefixo de palavras em que duas
  hipóteses consecutivas concordam; o resto é exibido como texto provisório.
  O buffer é cortado no fim da última frase confirmada (o overlap natural evita cortar
  palavras nas bordas). Texto confirmado vira `initial_prompt` para dar contexto.
- **Critério:** `python -m live_caption transcribe netflix` imprime linhas
  provisórias + confirmadas; log mostra latência por passada; um áudio conhecido
  tocado via `pw-play` sai com transcrição reconhecível e atraso < 3 s.
- **Riscos:** alucinações em silêncio/música ("Obrigado por assistir") → VAD +
  `no_speech_threshold`; auto-detecção de idioma "oscilando" → travar o idioma após
  as primeiras frases; configuração das libs CUDA (`LD_LIBRARY_PATH`).

## Mudança de rumo (2026-09-26) — legenda do player primeiro, áudio como reserva

**Uso real:** série tocando numa aba enquanto o dono trabalha no VS Code; a
janelinha sempre no topo é o que ele lê para acompanhar, sem olhar o vídeo. O
atraso de ~2 s do Whisper (p50 da confirmação) atrapalha esse uso, e as séries
já têm legenda PT-BR na Netflix.

**Nova direção:** onde o serviço tem legenda, pegá-la do player com uma extensão
do Firefox e mandar para o app local (atraso ~0, texto correto). O Whisper
(fases 1–2) vira reserva para o que não tem legenda. Tradução com Argos só
quando a legenda não está no idioma desejado. Música (Spotify): letra
sincronizada via MPRIS + LRCLIB, em vez de ASR.

```
extensão Firefox (legenda + tempo do vídeo) ──HTTP local──► app Python ──► overlay PySide6
PipeWire + Whisper (reserva, sem legenda) ─────────────────►
```

**Linguagem:** continua Python. O caminho novo é leve (receber texto e desenhar);
o app pesado (faster-whisper, Argos) é Python; a extensão é JS de qualquer jeito.
Tauri/Electron não resolvem melhor o "sempre no topo" no Wayland (GTK4 removeu
`keep_above`; Electron também depende do XWayland).

O plano completo das próximas fases é reescrito depois do spike abaixo, porque o
resultado dele decide entre ler a legenda da tela ou sincronizar o arquivo inteiro.

## Fase 3 — Spike: legenda direto do player (medido em 2026-09-26)

- **Código:** `spikes/subtitle_probe/` (roteiro de teste no README de lá);
  parser de TTML/WebVTT/json3 em `src/live_caption/subs/timedtext.py`.
- **Pergunta:** com a aba da Netflix atrás do VS Code, em outro workspace,
  minimizada ou em segundo plano, a legenda continua aparecendo na hora? No
  Wayland o compositor para de pedir quadros a janelas escondidas, e aba em
  segundo plano tem timers estrangulados; não dá para saber sem medir.
- **Critério:** `analyze.py` mostra, por cenário, atraso na tela vs. horário do
  arquivo de legenda e legendas perdidas.
- **Decisão que sai daqui:** se a legenda na tela chega em dia em todos os
  cenários → ler da tela (simples). Se não → interceptar o arquivo e sincronizar
  pelo `currentTime` do vídeo (robusto, permite pré-traduzir).
- **Resultado:** a legenda na tela chega igual em todos os cenários (visibilidade
  não importa), mas o renderizador da Netflix atrasa 0,4–1 s ~12% dos inícios de
  fala e pulou uma fala curta, mesmo visível. O arquivo inteiro do episódio é
  interceptado ao abrir o player. Detalhes em `spikes/subtitle_probe/README.md`.

## Tradução acoplada (era a fase 3; agora só para legenda em outro idioma)

- **Ferramentas:** `argostranslate` (roda em CPU, preserva VRAM para o Whisper).
- **Estratégia:** traduzir **só texto confirmado**, por frase — traduzir hipóteses
  provisórias faz a legenda "piscar" com traduções diferentes a cada passada.
  Instância do tradutor carregada uma vez e reutilizada.
- **Critério:** `python -m live_caption translate netflix --to pt` imprime pares
  original→tradução com tempo por frase (< ~300 ms em CPU).
- **Riscos:** par de idiomas inexistente → Argos faz pivô via inglês (perde
  qualidade); dependências pesadas de segmentação de frase (verificar o que a versão
  atual puxa); qualidade ruim em fragmentos curtos.

## Fase 4 — Overlay

- **Ferramentas:** `PySide6` (QWidget sem moldura, `WA_TranslucentBackground`,
  `WindowStaysOnTopHint`, `startSystemMove`), rodando via XWayland.
- **Critério:** primeiro com fonte de texto *fake* (timer), depois com o pipeline
  real: janela transparente, sempre no topo, arrastável, com 1–2 linhas de legenda
  sobre o Firefox tocando Netflix.
- **Riscos:** Wayland (ver acima); sobreposição a vídeo em **tela cheia** pode não
  funcionar no Mutter → usar o navegador maximizado; click-through (clicar "através"
  da legenda) fica fora da v1.

## Fase 5 — Latência e qualidade

- **Ferramentas:** instrumentação própria (timestamps por estágio), config em TOML.
- **O que fazer:** medir atraso ponta a ponta (captura → ASR → tradução → tela);
  variar passo, tamanho máximo do buffer, limiares de VAD, `small` vs `large-v3-turbo`;
  reconectar automaticamente quando o stream some (`StreamGone`) procurando o mesmo app.
- **Critério:** p50 de atraso ≤ ~2,5 s medido, e legenda fluida em 10 min de episódio.
  Cortes/repetições ocasionais são **esperados** — ajuste iterativo, não bug.

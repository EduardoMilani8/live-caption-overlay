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

**Decisão (2026-09-26, após o spike da fase 3): sincronizar o arquivo de legenda
pelo relógio do vídeo**, não ler da tela. A extensão intercepta o arquivo do
episódio inteiro e manda "âncoras" de tempo (`currentTime`, velocidade, pausado)
nos eventos do player; o app Python mantém o relógio e decide qual fala mostrar.
Assim o tempo sai exato (a tela da Netflix atrasa ~12% das falas), nenhuma fala
se perde, a aba escondida não importa (o app não depende de timers do navegador)
e o episódio pode ser traduzido inteiro de antemão.

```
Netflix ─► extensão: arquivo TTML + âncoras de tempo ─HTTP local─► app Python:
           parser → relógio → fala atual ─► overlay PySide6
```

## Fase 3 — Spike: legenda direto do player ✅

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

## Fase 4 — Extensão definitiva: arquivo + âncoras de tempo

- **Onde:** `extension/` na raiz (a do spike fica como referência).
- **O que faz:** reaproveita o `hook.js` do spike para interceptar o TTML; manda
  cada trilha **uma vez** com o id do título (`/watch/<id>`) e o idioma
  (`xml:lang`); manda âncoras `{t (relógio de parede), vt, rate, paused}` em
  `play`/`pause`/`seeked`/`ratechange`, na troca de episódio e a cada ~1 s via
  `timeupdate` (evento de mídia, não timer — não sofre estrangulamento). Continua
  mandando o texto da tela, só como verificação.
- **Trilha ativa:** a Netflix também baixa legendas das prévias e de outros
  idiomas; a ativa é a do título em `/watch/<id>` que casa com o texto da tela.
- **Critério:** com o receptor do spike, um episódio mostra 1 trilha do título
  certo, âncoras em cada play/pause/seek, e uma trilha nova ao passar para o
  próximo episódio ou trocar o idioma da legenda.
- **Riscos:** legenda desligada no player → a Netflix não baixa arquivo (manter
  ligada; esconder a da tela fica para depois); a Netflix mudar o formato.

## Fase 5 — App receptor + relógio (saída no terminal)

- **Onde:** `src/live_caption/subs/` (receptor HTTP, `SubtitleClock`), comando
  `python -m live_caption follow`.
- **Como:** o relógio extrapola `vt_agora = vt + (agora − t) × rate` a partir da
  última âncora (mesma máquina → mesmo relógio de parede, sem ajuste de fuso ou
  deriva); acha a fala atual por busca binária e agenda a próxima troca no app
  Python. A lógica do relógio é pura e testada com pytest (pausa, seek, 1,5×,
  âncora atrasada, falas sobrepostas).
- **Critério:** `follow` imprime cada fala no terminal no instante certo; comparado
  com o texto da tela que a extensão ainda manda, o início das falas erra
  p95 < 100 ms, sem falas perdidas, com a aba escondida.

## Fase 6 — Overlay

- **Ferramentas:** `PySide6` (QWidget sem moldura, `WA_TranslucentBackground`,
  `WindowStaysOnTopHint`, `startSystemMove`), rodando via XWayland.
- **Critério:** primeiro com fonte de texto *fake* (timer), depois ligado ao
  relógio da fase 5: janela transparente, sempre no topo, arrastável, 1–2 linhas
  de legenda legíveis por cima do VS Code enquanto a Netflix toca escondida.
- **Riscos:** Wayland (ver acima); sobreposição a vídeo em **tela cheia** pode não
  funcionar no Mutter → usar o navegador maximizado; click-through (clicar "através"
  da legenda) fica fora da v1.

## Fase 7 — Tradução quando a legenda não está em PT-BR

- **Ferramentas:** `argostranslate` (CPU).
- **Estratégia:** com o arquivo inteiro na mão, traduzir **o episódio todo ao
  receber a trilha** (em background, na ordem do tempo, a partir da posição atual),
  guardando em cache por título. Na hora de mostrar é só consulta — atraso zero.
- **Critério:** episódio só com legenda em inglês aparece em PT-BR no overlay;
  tempo para traduzir um episódio inteiro medido.
- **Riscos:** par sem modelo direto → pivô via inglês (perde qualidade); frases
  quebradas em duas falas traduzidas separadamente (juntar por pontuação).

## Fase 8 — Reservas: sem legenda (Whisper) e música (letras)

- **Whisper (fases 1–2 já prontas):** para o que não tem legenda; entra no mesmo
  overlay. Pendências herdadas: reconectar quando o stream some (`StreamGone`),
  `small` vs `large-v3-turbo` medido na tomada, palavra perdida na fronteira de
  confirmação. Cortes/repetições ocasionais são **esperados** — ajuste iterativo.
- **Música (Spotify):** título e posição via MPRIS + letra sincronizada do LRCLIB,
  reaproveitando o relógio da fase 5.
- **Critério:** vídeo sem legenda mostra transcrição com atraso p50 ≤ ~2,5 s;
  música com letra mostra a linha certa.

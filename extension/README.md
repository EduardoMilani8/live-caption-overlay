# Extensão do Firefox (fase 4)

Manda para o app, em `POST http://127.0.0.1:8765/event` (JSON), a legenda que a
Netflix baixa e o relógio do vídeo em **tempo de conteúdo**. Não desenha nada:
quem mostra a legenda é o app (fases 5–6).

- `page.js` — roda no mundo da página (`world: MAIN`), o único que vê o arquivo
  de legenda baixado pelo player e a API interna do player da Netflix.
- `content.js` — só repassa as mensagens do `page.js` para o background.
- `background.js` — faz o POST; o ícone mostra `ERR` se o app não responde.

## Carregar (temporária, até fechar o Firefox)

`about:debugging#/runtime/this-firefox` → **Carregar extensão temporária…** →
`extension/manifest.json`, e recarregar a aba da Netflix. Sem cliques, com o
Firefox aberto com `--start-debugger-server 6000` (ver o README do spike):

```sh
.venv/bin/python spikes/subtitle_probe/load_extension.py --dir extension
```

A legenda precisa estar **ligada** no player (em qualquer idioma que se queira
ver): sem isso a Netflix não baixa arquivo nenhum.

## Mensagens

Todas têm `type`, `t` (relógio de parede em ms, `Date.now()`, no instante da
amostra), `path` e `tab` (posto pelo background).

| `type` | Quando | Campos |
|---|---|---|
| `track` | cada arquivo de legenda, uma vez | `kind: "ttml"`, `url`, `lang`, `fileLang`, `movieId`, `prefetch`, `size`, `body` |
| `anchor` | `play`, `pause`, `seeked`, `ratechange`, `loadedmetadata`, `emptied`, borda de anúncio (`reason: "ad"`), e `timeupdate` no máx. 1/s | `reason`, `vt`, `rate`, `paused`, `ad`, `clock`, `movieId` |
| `cue` | o texto da legenda na tela mudou (só para conferência) | `text` (linhas com `\n`, `""` = sumiu), mais os campos da âncora |

- `vt` (s) é tempo de **conteúdo**: `video.currentTime − (getCurrentTime() −
  getSegmentTime())/1000`, porque o anúncio é emendado no mesmo `<video>` e o
  `currentTime` conta o anúncio. Durante o anúncio, `vt` fica parado no ponto do
  intervalo.
- `paused` = o relógio do conteúdo **não anda** (pausado ou anúncio na tela);
  fora disso, `vt_agora = vt + (agora − t)/1000 × rate`.
- `ad` = anúncio na tela: não mostrar legenda.
- `clock` = `"content"` (API do player) ou `"video"` (API sumiu: `vt` conta os
  anúncios; confiar na conferência pelo texto da tela).
- `lang` vem do idioma **selecionado no player** na hora do download; o
  `xml:lang` do arquivo (`fileLang`) pode estar errado (visto: arquivo pt-BR
  dizendo `en`).
- `prefetch: true` = segundo arquivo do mesmo episódio no mesmo idioma, tratado
  como o do **próximo** episódio (a Netflix baixa ~3 min antes do fim); o
  `movieId` então vem do *post-play* da página. É um palpite: o app confirma a
  trilha ativa pelo texto da tela.

## Verificação (2026-09-26, Netflix com anúncios, House T5E2–E4, PT-BR, Firefox 156 snap)

Com o receptor do spike (`server.py`) e `drive.py` fazendo as ações pela API do
player, aba visível sem foco:

- Abrir o episódio: **1 trilha**, `pt-BR`, episódio certo; falas da tela em
  tempo de conteúdo.
- `pause`/`play`/`seek`/`1,5×`: uma âncora em cada; o seek gera
  `pause`→`seeked`→`play`; a 1,5× o `vt` andou 6,05 s em 4 s.
- Trocar para inglês: trilha nova `en`. Voltar para pt-BR: nenhuma trilha (já
  estava em cache) — a ativa se descobre pelo texto da tela.
- Próximo episódio sozinho (*autoplay*): a trilha dele chega ~3 min antes do fim
  com `movieId` do próximo e `prefetch: true`; a primeira versão rotulava com o
  episódio atual e `lang` do arquivo (`en`) — corrigido.
- **Anúncio pre-roll de ~15 s** no começo do E4: âncoras `paused` + `ad` com `vt`
  parado em 0 do começo ao fim; depois, 109 falas da tela casaram com o arquivo
  com deslocamento **mediano de 17 ms** (o `<video>` ficou 15,1 s à frente).
  Na borda final o `vt` saltou de 0 para 1,08 s — provavelmente o fim do anúncio
  é sinalizado ~1 s depois de o conteúdo voltar (não confirmado); não afetou
  fala nenhuma (a primeira começa em ~4,9 s).
- A primeira âncora de uma página é um `timeupdate`: os `loadedmetadata`/`play`
  iniciais acontecem com o `<video>` ainda fora do documento, onde o listener
  não alcança.
- Não medido: pausar **durante** um anúncio — o intervalo seguinte (16:47 do E4) veio
  vazio (a Netflix serve anúncio raramente, como no teste 2 do spike).

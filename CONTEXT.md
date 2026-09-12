# CONTEXT.md — Game-Core

Contexto **Jogo**: simula as partidas e serve o que se lê sobre elas. É o serviço com
o código mais delicado do sistema — a física do Pong vive aqui.

> Submodule de [Transcendence](https://github.com/Meia-noite-eu-te-conto/Transcendence).
> Regras transversais em `AGENTS.md` na raiz. Este serviço é **legado**: seu substituto
> é o serviço `game` em Go (onda 3 da migração).

## Responsabilidade

- Receber o pedido de criação de partida, criar o registro e **simular** o jogo.
- Aceitar input dos jogadores por WebSocket e devolver o estado do campo.
- Publicar o ciclo de vida da partida (`game-created`, `game-started`, `game-over`).
- Servir ranking de jogadores e histórico de partidas de um torneio.

Não sabe nada de sala, de chaveamento ou de quem é dono do quê. Recebe uma lista de
jogadores com cores e simula.

## Stack

Django 5.1 + Channels 4.2 + uvicorn (ASGI), Postgres, Redis. Python 3.11.
Sem DRF — são `django.views.View` com `JsonResponse` na mão.

## Estrutura

```
src/
├── game_project/            projeto Django
│   ├── settings.py          SECRET_KEY fixo no código; DEBUG=False; sem apps de auth
│   ├── asgi.py              ProtocolTypeRouter: http + websocket
│   ├── urls.py              /api/v1/game-core/games/
│   └── middlewares.py       ExceptionMiddleware → 500 genérico
├── games_app/               API e WebSocket
│   ├── models/              GameModel, PlayerModel, ScoreModel
│   ├── consumers/GameSessionConsumer.py    WS de jogo
│   ├── repositories/game_repository.py     acesso a dados (async)
│   ├── views/games_view.py                 ranking
│   ├── views/tournament_history_view.py    histórico
│   └── routing.py           ws: /api/v1/game-core/games/<game_id>/<user_id>/
└── games_worker/            o motor do jogo
    ├── management/commands/session_worker.py   entrypoint do worker
    ├── listeners/game_maker_listener.py       consome create-game-queue
    ├── game_core/game_session.py              ★ o loop de simulação
    └── utils/{game_config.py,ball.py,player.py}
```

## Os arquivos que importam

**`games_worker/game_core/game_session.py`** é o coração: loop a ~50 Hz,
colisão bola/parede/paddle para 2 e 4 jogadores, colisão de canto por distância
euclidiana, reset de bola com pausa de 3 s, bot para single-player, detecção de gol,
fim de jogo em 5 pontos, e publicação do resultado. **É a especificação da física** —
o comportamento observável dele é o que o Go precisa reproduzir.

**`games_worker/utils/game_config.py`** tem todas as constantes: campo 90×80
(80×80 em 4 jogadores), paddle 2×16, bola raio 2, velocidade do paddle 4,
velocidade da bola entre 0,4 e 0,7 por tick, 5 pontos para ganhar.

**`games_app/consumers/GameSessionConsumer.py`** traduz tecla em direção (±1) pelo
mapa `directions_by_color` e empurra num buffer Redis por jogador.

## Como roda

Dois processos a partir do mesmo código:

| Container | Comando | Papel |
| --- | --- | --- |
| `game-core` | `uvicorn game_project.asgi:application --port 8001` | HTTP + WebSocket |
| `game-worker` | `python manage.py session_worker` | simulação |
| `game-core-migrate` | `python manage.py migrate` | one-shot |

Sobe pelo compose da raiz (`make`), não isoladamente.

## Integração

**Consome** — lista Redis `create-game-queue` (`LPOP` a cada 1 s):
```json
{"type":"create_game","roomId":"...","roomType":0,"matchId":"...",
 "isSinglePlayer":false,"stage":1,"ownerId":"...",
 "players":[{"id":"...","name":"...","color":0}]}
```

**Publica** — lista Redis `game-sync-session-queue`:
`{"type":"game-created"|"game-started"|"game-over", "matchId", "gameId", ...}`.
Em `game-over` vai também `winner` e `players[{rank,id,score}]`.

**Channel layer Redis** — grupos `game_session_{gameId}` (snapshot e placar) e
`room_{roomCode[:8]}_{matchId}` (avisar a sala que o jogo começou).

**Buffer de input** — lista Redis `game_session_{gameId}_{userId}`, escrita pelo
consumer e lida pelo worker a cada 5 ms.

## API

```
GET /api/v1/game-core/games/ranking/?page=&pageSize=
GET /api/v1/game-core/games/tournament-history/?roomCode=&player_name=&page=&pageSize=
WS  /api/v1/game-core/games/{gameId}/{userId}/
```

No WS: cliente manda `{"direction":"w"|"s"|"a"|"d"}`; servidor manda
`game.update` (snapshot), `update_score` e `game_finished`.

## Armadilhas deste serviço

- **Tick que desliza.** `await asyncio.sleep(0.02)` vem *depois* do trabalho, então o
  passo real é 20 ms + processamento. Sob carga, a bola anda mais devagar.
- **Banco dentro do loop.** `update_score` grava a cada ponto e
  `check_players_connected` consulta jogadores a cada segundo, em laço.
- **Estado só em memória.** `GameMakerListener.game_sessions` é um `dict` no processo:
  reiniciar o worker mata todas as partidas, e duas réplicas não coexistem.
- **RNG sem semente.** `GameConfig.ball_speed_x/y` são avaliados **na importação** —
  toda partida do processo começa com a mesma direção de bola.
- **`playerColor` herda de `enumerate`**, não de `Enum`, e pula o valor 2
  (`GREEN = 3, YELLOW = 4`). Divergente dos mapas do `User-Session` e do front-end.
- **Snapshot serializado duas vezes** (`json.dumps` dentro de `json.dumps`), a 50 Hz.
- **`redis.Redis(host='redis')` fixo** no consumer e em `games/views.py`, ignorando
  `REDIS_HOST` que o resto do código lê do ambiente.
- **`task = lista.append(...)`** em `add_player_channels` guarda `None`.
- **`send()` sem tratar socket já fechado.** `update_score` e `game_update` chamam
  `self.channel_layer.group_send` sem capturar erro de envio; se um jogador cai no
  meio de um broadcast (aba fechada, rede caindo), o Channels loga um
  `RuntimeError: Unexpected ASGI message 'websocket.send' after ...` — não derruba o
  pod, mas cada desconexão abrupta de jogador de verdade produz esse stack trace no log.
  Confirmado subindo o serviço no cluster (TK.12) e fechando a conexão logo após o
  handshake.
- **Zero testes.**

Detalhe de cada item em [docs/migration/01-analise-atual.md](../docs/migration/01-analise-atual.md).

## Para onde vai

Serviço `game` em Go, com dois binários:

| Aqui | Vai para |
| --- | --- |
| `games_app/consumers/` | `cmd/game-api` — WS, stateless, escala horizontal |
| `games_worker/` | `cmd/game-engine` — simulação com lease em Redis e tick fixo de 60 Hz |
| `games_worker/game_core` + `utils` | `internal/domain/pong` — física pura, determinística, sem I/O |
| `views/` (ranking, histórico) | serviço `stats` (onda 1, antes deste) |
| listas Redis | NATS JetStream com ack |

A física é portada com **teste de golden file**: instrumenta-se este código com RNG
semeado, gravam-se N ticks de estado, e o Go precisa reproduzir dentro de epsilon.
Ver a skill `port-game-loop` e o [ADR-0004](../docs/adr/0004-simulacao-autoritativa.md).

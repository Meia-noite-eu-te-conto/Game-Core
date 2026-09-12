// Teste de fumaça e carga leve do Game-Core, contra uma instância local
// (subida pelo próprio job de CI, com Postgres e Redis reais — sem mock).
//
// Cobre só os endpoints REST de leitura (ranking, histórico de torneio).
// Criar e simular uma partida de verdade exige orquestração com o
// User-Session (fila Redis + dois serviços) — fora do escopo de um teste
// isolado de CI de um serviço só; esse fluxo é validado manualmente contra
// o cluster (ver docs/deploy/03-legado-no-k8s.md, TK.12).
//
// Contrato usado aqui é o ATUAL do Game-Core (commit d1af81b): resposta
// envelopada em "paginatedItems.Data" — diferente do User-Session, que não
// usa esse envelope. Não confundir os dois formatos ao portar/comparar.
//
// Rodar localmente: k6 run -e BASE_URL=http://localhost:8001 k6/smoke.js
import http from "k6/http";
import { check } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8001";
const API = `${BASE_URL}/api/v1/game-core/games`;

const rankingDuration = new Trend("ranking_duration", true);
const historyDuration = new Trend("tournament_history_duration", true);

export const options = {
  scenarios: {
    ranking: {
      executor: "constant-vus",
      vus: 5,
      duration: "15s",
      exec: "ranking",
    },
    tournament_history: {
      executor: "constant-vus",
      vus: 3,
      duration: "15s",
      exec: "tournamentHistory",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    ranking_duration: ["p(95)<500"],
    tournament_history_duration: ["p(95)<500"],
  },
};

export function ranking() {
  const res = http.get(`${API}/ranking/?page=1&pageSize=10`);
  rankingDuration.add(res.timings.duration);
  check(res, {
    "ranking: 200 ou 204 (204 se não há dados agregáveis)": (r) => r.status === 200 || r.status === 204,
    "ranking: envelope paginatedItems presente quando 200": (r) =>
      r.status !== 200 || r.json("paginatedItems") !== undefined,
  });
}

export function tournamentHistory() {
  // roomCode é obrigatório (a view devolve 400 sem ele) — usa um prefixo
  // que não bate com sala nenhuma, só para exercitar o caminho de "sem
  // resultado" sem depender de uma sala real existir no banco de teste.
  const res = http.get(`${API}/tournament-history/?roomCode=k6-nao-existe`);
  historyDuration.add(res.timings.duration);
  check(res, {
    "historico de torneio: 200": (r) => r.status === 200,
    "historico de torneio: paginacao presente": (r) => typeof r.json("currentPage") === "number",
    "historico de torneio: games e uma lista": (r) => Array.isArray(r.json("games")),
  });
}

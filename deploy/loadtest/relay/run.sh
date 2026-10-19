#!/usr/bin/env bash
# SSE 릴레이 부하 측정 한 회차를 돌린다.
#
#   deploy/loadtest/relay/run.sh <label> <동시연결수> [relay_load.py 추가 인자...]
#   예) OUT_DIR=/tmp/relay deploy/loadtest/relay/run.sh L1-200 200
#       deploy/loadtest/relay/run.sh L2-400 400 --drop-frac 0.2
#
# 하는 일: member 로 로그인 → 대화 1개 생성 → PENDING job N개를 DB 에 직접 넣는다(Kafka 미발행이라
# rag-service 가 집지 않는다) → 도커 네트워크 안의 일회용 컨테이너에서 생산자·클라이언트 실행 →
# 그동안 2초마다 서버 지표 수집 → job·대화 삭제.
# 비밀값(Redis 비밀번호)은 실행 중인 redis 컨테이너의 환경변수에서 바로 넘긴다. 화면에 찍지 않는다.
set -euo pipefail
cd "$(dirname "$0")/../../.."

LABEL=$1; N=$2; shift 2
OUT_DIR=${OUT_DIR:-$(mktemp -d)}
mkdir -p "$OUT_DIR"
NET=org-decision-history_backend
IMAGE=relay-loadtest:1

psql_q() { docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d knowledge -v ON_ERROR_STOP=1 -qAt'; }
redis_q() { docker compose exec -T redis sh -c "redis-cli -a \"\$REDIS_PASSWORD\" --no-auth-warning $*"; }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -q -t "$IMAGE" - <<'EOF' >/dev/null
FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1 redis==5.2.1
EOF
fi

# 회차를 연달아 돌리면 게이트웨이 로그인 레이트 리밋에 걸릴 수 있어 몇 번 다시 시도한다
TOKEN=""
for i in 1 2 3 4 5 6; do
  TOKEN=$(curl -sf -X POST http://127.0.0.1:8080/api/auth/login -H 'Content-Type: application/json' \
    -d '{"email":"member@company.com","password":"password123"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])' 2>/dev/null) && break
  sleep 10
done
[[ -n "$TOKEN" ]] || { echo "로그인 실패" >&2; exit 1; }
CONV=$(curl -sf -X POST http://127.0.0.1:8080/api/conversations -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

cleanup() {
  if [[ -n "${MON_PID:-}" ]]; then kill "$MON_PID" 2>/dev/null || true; wait "$MON_PID" 2>/dev/null || true; fi
  echo "DELETE FROM chat.answer_job WHERE conversation_id = $CONV; DELETE FROM chat.conversation WHERE id = $CONV;" | psql_q >/dev/null
}
trap cleanup EXIT

JOBS="$OUT_DIR/$LABEL.jobs"
echo "INSERT INTO chat.answer_job (job_key, conversation_id, user_id, question, status)
      SELECT gen_random_uuid(), c.id, c.owner_id, 'loadtest-relay', 'PENDING'
      FROM chat.conversation c, generate_series(1, $N) WHERE c.id = $CONV
      RETURNING job_key;" | psql_q > "$JOBS"

# 서버 지표: 시각, chat-service 라이브 스레드, Redis 연결 수·누적 연결 수, chat-service/redis CPU
MON="$OUT_DIR/$LABEL.monitor.csv"
echo "epoch,chat_threads,redis_clients,redis_total_conn,chat_cpu,redis_cpu,gateway_cpu" > "$MON"
(
  while true; do
    t=$(date +%s)
    th=$(docker compose exec -T chat-service sh -c 'wget -qO- http://localhost:8084/actuator/prometheus' 2>/dev/null \
         | awk '/^jvm_threads_live_threads/{print $2}')
    info=$(redis_q "info clients; " 2>/dev/null; redis_q "info stats" 2>/dev/null)
    rc=$(echo "$info" | awk -F: '/^connected_clients/{print $2}' | tr -d '\r')
    rt=$(echo "$info" | awk -F: '/^total_connections_received/{print $2}' | tr -d '\r')
    cpu=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' \
          org-decision-history-chat-service-1 org-decision-history-redis-1 org-decision-history-gateway-1 2>/dev/null \
          | awk '{gsub("%","",$2); printf "%s,", $2}')
    echo "$t,$th,$rc,$rt,${cpu%,}" >> "$MON"
  done
) &
MON_PID=$!

redis_q "config resetstat" >/dev/null
docker run --rm --network "$NET" \
  -v "$PWD/deploy/loadtest/relay:/lt:ro" -v "$OUT_DIR:/out" \
  --env-file <(docker compose exec -T redis sh -c 'printf "REDIS_PASSWORD=%s\n" "$REDIS_PASSWORD"') \
  -e REDIS_HOST=redis \
  "$IMAGE" python /lt/relay_load.py --token "$TOKEN" --conv "$CONV" \
    --jobs-file "/out/$LABEL.jobs" --out "/out/$LABEL.json" "$@"

sleep 4   # 끊긴 연결 정리 추이를 조금 더 본다
redis_q "info commandstats" | grep -E 'cmdstat_(xread|xrange|xadd|publish|expire)' > "$OUT_DIR/$LABEL.commandstats" || true
echo "결과: $OUT_DIR/$LABEL.json, $MON, $OUT_DIR/$LABEL.commandstats"

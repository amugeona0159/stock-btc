# 프론트와 API 를 한 이미지에. 원본이 하나라 CORS 를 안 열어도 된다.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY server/ ./server/
# `research` 는 뺀다 — arch·statsmodels 는 연구용 스크립트만 쓰는데 이미지가 두 배가 된다.
RUN pip install --no-cache-dir -e ".[ml,push]"

COPY --from=web /web/dist ./web/dist
COPY scripts/ ./scripts/
# **학습 기록을 같이 넣는다.** `api/learning.py`·`api/recommend.py` 는 저장소 뿌리
# 기준으로 `learning/` 을 읽는다(`MARKET_LENS_LEARNING` 은 스크립트용이고 서버는
# 학습을 안 돌린다). 이게 없으면 배포판에서 아침 추천·챔피언·성적이 통째로 비는데,
# 화면은 "아직 없다"고만 말해서 원인이 안 보인다.
# 판 원자료(`study/verdicts.jsonl`)는 `.dockerignore` 가 뺀다 — 수만 줄이고
# 화면은 요약만 읽는다.
COPY learning/ ./learning/

# 감시 루프를 켠다. 이게 없으면 알림이 안 나간다.
ENV MARKET_LENS_WATCH=1
# 알림 파일이 볼륨에 남게 한다(재배포해도 규칙이 안 사라진다).
ENV MARKET_LENS_ALERTS=/data/alerts
ENV MARKET_LENS_LEARNING=/data/learning

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "marketlens.api.app:app", \
     "--app-dir", "server", "--host", "0.0.0.0", "--port", "8080"]

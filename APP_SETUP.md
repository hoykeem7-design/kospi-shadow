# KOSPI Shadow Coach 앱 설치

## 1. 최초 실행

GitHub 저장소의 Actions에서 **KOSPI Shadow Coach App**을 열고 `Run workflow`를 누릅니다.
성공하면 `kospi-shadow-coach-*` 아티팩트에 설치 가능한 정적 PWA가 생성됩니다.

## 2. 휴대폰 주소로 자동 배포

저장소가 GitHub Pages를 사용할 수 있는 요금제라면:

1. Repository Settings → Pages → Source를 **GitHub Actions**로 선택
2. Settings → Secrets and variables → Actions → Variables → New repository variable
3. 이름 `ENABLE_GITHUB_PAGES`, 값 `true` 등록
4. **KOSPI Shadow Coach App**을 다시 실행

그 뒤 생성된 Pages 주소를 Chrome에서 열고 `홈 화면에 추가`하면 앱처럼 사용할 수 있습니다.

GitHub Free의 비공개 저장소는 Pages 배포가 지원되지 않을 수 있습니다. 이 경우 예측 코드와 키는 비공개 저장소에 유지하고, 생성된 `site/`만 별도 호스팅하는 방식이 필요합니다.

## 자동 업데이트 시각(한국시간)

- 07:45 장전 종합 브리핑
- 08:10 NXT 프리마켓 초기 확인
- 08:47 KOSPI200 선물 확인
- 09:10 본장 10분 확인
- 12:00 정오 재평가
- 15:20 마감 점검
- 20:05 애프터·야간선물 기반 익일 계획

## 중요한 한계

- 기존 예측 모델은 일간 시가→종가 방향 모델입니다.
- 분 단위 최적 진입 시각은 별도 실시간 확인 레이어이며 아직 독립적으로 백테스트된 확률모델이 아닙니다.
- 정적 PWA는 NXT WebSocket을 상시 구독하지 않습니다. 08:00~08:45에는 안전하게 선물 개장 확인을 기다립니다.
- 자동주문 기능은 포함하지 않습니다.

## Automatic Netlify production updates (v4.1)

The Coach workflow now refreshes the prediction, rebuilds `site/`, and deploys it to the existing Netlify production site at the configured checkpoints.

Required GitHub Actions repository secrets:

- `NETLIFY_AUTH_TOKEN`: Netlify personal access token.
- `NETLIFY_SITE_ID`: Netlify project ID or project name. For the current site, `joyful-crostata-e96663` is accepted by Netlify CLI.

The app refresh button checks the latest deployed `data/dashboard.json`. It does not expose API credentials and does not trigger a GitHub workflow from the browser. Scheduled GitHub Actions runs are responsible for collecting data and publishing the new production deploy.

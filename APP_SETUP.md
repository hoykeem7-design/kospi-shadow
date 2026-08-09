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

- 07:30 장전 모델·브리핑, 20:05 마감 모델·익일 준비
- 08:00~08:50은 10분마다 NXT·시장 스냅샷
- 09:00~15:50은 10분마다 장중 시장 스냅샷(09:05 시초 5분, 15:45 마감 확인 추가)
- 16:00~20:40은 20분마다 애프터마켓 스냅샷

스케줄은 GitHub Actions 실행 목표 시각이며, 플랫폼 부하에 따라 몇 분 지연될 수 있습니다. 화면은 수신 시각이 기준을 넘으면 자동으로 신규 판단을 잠급니다.

## 중요한 한계

- 기존 예측 모델은 일간 시가→종가 방향 모델입니다.
- 분 단위 최적 진입 시각은 별도 실시간 확인 레이어이며 아직 독립적으로 백테스트된 확률모델이 아닙니다.
- 정적 PWA는 NXT WebSocket을 상시 구독하지 않습니다. 08:00~08:45에는 안전하게 선물 개장 확인을 기다립니다.
- 자동주문 기능은 포함하지 않습니다.

## GitHub Pages 자동 배포

현재 운영 주소는 GitHub Pages입니다. 브라우저의 `최신 데이터` 버튼은 배포된 `data/dashboard.json`만 다시 확인하며, API 키를 노출하거나 GitHub workflow를 직접 실행하지 않습니다. 정기 GitHub Actions가 데이터를 수집하고 Pages에 새 스냅샷을 배포합니다.

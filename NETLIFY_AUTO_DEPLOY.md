# Netlify 자동 배포 설정

Coach 앱 v4.1은 GitHub Actions가 한국 시간 기준 07:45, 08:10, 08:47, 09:10, 12:00, 15:20, 15:35, 20:05에 새 데이터를 만들고 Netlify 운영 주소에 자동 배포합니다.

GitHub 저장소의 `Settings > Secrets and variables > Actions`에서 다음 두 Repository secret을 추가합니다.

## NETLIFY_AUTH_TOKEN

Netlify 사용자 설정의 `Applications > Personal access tokens`에서 새 토큰을 생성하고 값을 저장합니다. 토큰 값은 앱 파일이나 채팅에 붙여 넣지 않습니다.

## NETLIFY_SITE_ID

현재 프로젝트는 아래 프로젝트 이름을 사용할 수 있습니다.

```text
joyful-crostata-e96663
```

Netlify의 실제 Project ID를 쓰려면 `Project configuration > General > Project information`에서 복사해도 됩니다.

설정 후 GitHub Actions의 `KOSPI Shadow Coach App`을 수동 실행합니다. 성공하면 `joyful-crostata-e96663.netlify.app` 운영 주소가 자동 교체됩니다.

앱의 새로고침 버튼은 최신 Netlify 배포본을 확인합니다. 브라우저에서 GitHub Actions를 직접 실행하지 않으므로 API 키나 GitHub 토큰이 앱에 포함되지 않습니다.

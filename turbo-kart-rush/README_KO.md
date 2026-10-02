# Turbo Kart Rush (한국어 안내)

Three.js로 만든 브라우저용 카트 레이싱 게임입니다. 원본 저장소는 https://github.com/bridge-mind/turbo-kart-rush 이며, 이 폴더는 원본을 그대로 복사한 것입니다. (GitHub Pages 배포용 워크플로우 파일만 제외했습니다.)

## 바로 해보기 (설치 없이)

원본 제작자가 공개한 주소에서 바로 실행됩니다.

- https://bridge-mind.github.io/turbo-kart-rush/

## 내 PC(Windows)에서 실행하기

1단계 → https://nodejs.org 에서 Node.js LTS 버전을 설치합니다. (처음 한 번만)

2단계 → 이 폴더(`turbo-kart-rush`)에서 `run.bat` 파일을 더블클릭합니다.

3단계 → 검은 창이 뜨고 잠시 후 브라우저가 자동으로 열립니다. 열리지 않으면 브라우저 주소창에 아래 주소를 입력합니다.

```
http://localhost:5178/
```

게임을 끝내려면 검은 창을 닫으면 됩니다.

## 조작 방법

| 동작 | 키보드 |
| --- | --- |
| 가속 | W 또는 ↑ |
| 브레이크 / 후진 | S 또는 ↓ |
| 방향 | A, D 또는 ←, → |
| 점프 / 드리프트 | Space 또는 Shift |
| 아이템 사용 | E, Ctrl 또는 Enter |
| 뒤 보기 | Q |
| 일시정지 | Esc 또는 P |

게임패드도 지원합니다.

## 명령어로 직접 실행하고 싶을 때

명령 프롬프트에서 한 줄씩 입력합니다.

```
cd turbo-kart-rush
```

```
npm install
```

```
npm run dev
```

## 주의

- WebGL2를 지원하는 데스크톱 브라우저(Chrome, Edge, Firefox, Safari)에서 실행됩니다.
- 이 게임은 위너에어컨 업무용 도구가 아니라 별도 프로젝트이며, 정책 모니터 파일과는 전혀 연결되어 있지 않습니다.
- 라이선스는 원본과 같은 MIT입니다. (`LICENSE` 파일 참고)

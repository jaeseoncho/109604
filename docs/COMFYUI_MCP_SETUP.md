# ComfyUI MCP 설치 가이드

Claude Code(또는 Claude Desktop, Cursor 등 MCP 클라이언트)에서 ComfyUI를 직접 조작할 수 있게 해주는 **공식 Comfy MCP 서버(`comfy-mcp`)** 설치 방법입니다. 설치가 끝나면 Claude에게 자연어로 이미지·영상 생성, 워크플로 실행, 모델·노드 조회 등을 요청할 수 있습니다.

이 저장소에는 프로젝트 수준 설정 파일 [`.mcp.json`](../.mcp.json)이 포함되어 있어, 아래 사전 준비만 마치면 이 저장소를 여는 Claude Code 세션에서 자동으로 ComfyUI MCP가 연결됩니다.

## 1. 사전 준비

| 항목 | 설명 |
|------|------|
| Python 3.10+ | `comfy-mcp` 실행에 필요 |
| ComfyUI | 로컬 실행 중이어야 함 (기본 주소 `http://127.0.0.1:8188`) |
| comfy-cli (권장) | `comfy-mcp`는 comfy-cli의 얇은 래퍼이므로 함께 설치 권장 |

```bash
pip install comfy-mcp comfy-cli
```

ComfyUI가 아직 없다면 [ComfyUI Desktop](https://www.comfy.org/)을 설치하거나 comfy-cli로 설치할 수 있습니다:

```bash
comfy install     # ComfyUI 설치
comfy launch      # ComfyUI 실행 (기본 포트 8188)
```

> **중요:** 두 개의 프로세스가 필요합니다. `comfy launch`(또는 ComfyUI Desktop)가 ComfyUI 본체를 실행하고, MCP 클라이언트(Claude Code 등)가 `comfy-mcp`를 stdio 서버로 자동 실행합니다.

## 2. Claude Code에서 사용

### 방법 A — 이 저장소의 `.mcp.json` 사용 (기본)

이 저장소 루트의 `.mcp.json`이 프로젝트 범위 MCP 설정입니다. 저장소를 Claude Code로 열면 승인 여부를 묻고, 승인하면 `comfyui` 서버가 연결됩니다. 별도 명령이 필요 없습니다.

ComfyUI가 다른 주소에서 실행 중이면 환경 변수로 재정의합니다:

```bash
export COMFYUI_URL=http://192.168.0.10:8188   # 예: 원격 GPU 머신
```

### 방법 B — 사용자 범위로 등록 (모든 프로젝트에서 사용)

```bash
claude mcp add --scope user comfyui \
  -e COMFYUI_URL=http://127.0.0.1:8188 \
  -- comfy-mcp
```

연결 상태는 Claude Code 안에서 `/mcp` 명령으로 확인합니다.

## 3. Claude Desktop에서 사용

`claude_desktop_config.json`(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`)에 추가:

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "comfy-mcp",
      "env": {
        "COMFYUI_URL": "http://127.0.0.1:8188"
      }
    }
  }
}
```

## 4. Comfy Cloud 사용 (선택, 베타)

로컬 GPU 없이 Comfy Cloud(폐쇄 베타)를 쓰는 경우, 호스팅 MCP 서버에 OAuth로 연결합니다:

```bash
claude mcp add --transport http comfy-cloud https://cloud.comfy.org/mcp
```

이후 Claude Code에서 `/mcp`를 열어 로그인(OAuth)을 완료합니다. 자세한 내용은 [Comfy 공식 문서](https://docs.comfy.org/agent-tools/mcp)를 참고하세요.

## 5. 환경 변수 정리

| 변수 | 용도 |
|------|------|
| `COMFYUI_URL` | 대상 ComfyUI 주소 (기본 `http://127.0.0.1:8188`) |
| `COMFYUI_HOST` / `COMFYUI_PORT` | URL 대신 호스트·포트로 지정 |
| `COMFY_BIN` | comfy-cli 실행 파일 경로 (PATH에 없을 때 필수) |
| `COMFY_API_KEY` | Comfy 파트너 API 노드용 자격 증명 (선택) |
| `COMFY_MCP_DEBUG_LOG` | 실패 로그 활성화 (문제 해결용) |

## 6. 문제 해결

- **서버가 연결되지 않음** — `comfy-mcp`가 PATH에 있는지 확인: `which comfy-mcp`. 가상환경에 설치했다면 `.mcp.json`의 `command`를 절대 경로로 바꾸거나 `COMFY_BIN`을 지정하세요.
- **도구 호출이 실패함** — ComfyUI 본체가 실행 중인지, `COMFYUI_URL` 주소로 브라우저 접속이 되는지 확인하세요.
- **원격 GPU 사용** — GPU 머신에서 `comfy launch --listen 0.0.0.0` 후 로컬에서 `COMFYUI_URL=http://<GPU-IP>:8188` 설정.
- **상세 로그** — `COMFY_MCP_DEBUG_LOG=1`을 env에 추가해 실패 원인을 기록하세요.

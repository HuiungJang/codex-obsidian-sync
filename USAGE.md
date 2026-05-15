# codex-obsidian-sync Usage Guide

## 목적

`Codex CLI`와 `Codex Desktop`이 남기는 `~/.codex` 로그를 읽어,
Obsidian vault에 아래 노트를 자동으로 만든다.

- conversation note
- daily note
- project note

기본 실행 모델은 `macOS + launchd`다.
사용자는 한 번 `setup`만 해 두면, 이후 `Codex.app`와 `codex`를 평소처럼 사용하면 된다.

## 명령 실행 방식

## 권장 설치 방식

가장 단순한 실행 방식은 `pipx` 설치다.

```bash
cd /path/to/codex-obsidian-sync
pipx install -e .
pipx ensurepath
```

이후에는 어디서든 아래처럼 바로 실행할 수 있다.

```bash
codex-obsidian-sync status
```

왜 `pipx`를 권장하나:

- Homebrew Python 환경에서 `python3 -m pip install -e .`는 `externally-managed-environment`로 막힐 수 있다
- 이 도구는 Python library보다 Python app에 가깝다
- `pipx`는 독립 venv를 관리하면서 실행 파일만 노출해 준다

검증 결과:

- `pipx install -e .` 설치 성공 확인
- 설치 직후 `codex-obsidian-sync status --json` 실행 성공 확인

## 대안 실행 방식

### 1. `pipx` 설치형 명령 사용

패키지를 설치했다면 아래처럼 바로 실행한다.

```bash
codex-obsidian-sync <command> [options]
```

예시:

```bash
codex-obsidian-sync status
```

### 2. venv 사용

`pipx`를 쓰기 싫다면 전용 venv를 하나 두는 방법도 있다.

```bash
cd /path/to/codex-obsidian-sync
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

이후 실행:

```bash
.venv/bin/codex-obsidian-sync status
```

장점:

- 시스템 Python을 건드리지 않는다
- 설치가 단순하다

단점:

- 전역 명령처럼 바로 쓰려면 alias 또는 PATH 조정이 필요하다
- shell/session마다 activation 또는 절대 경로 호출이 필요하다

### 3. repo에서 바로 실행

아직 설치하지 않았다면 repo에서 module form으로 실행한다.

```bash
cd /path/to/codex-obsidian-sync
PYTHONPATH=src python3 -m codex_obsidian_sync.cli <command> [options]
```

예시:

```bash
cd /path/to/codex-obsidian-sync
PYTHONPATH=src python3 -m codex_obsidian_sync.cli status
```

이 문서 아래 예시에서는 짧게 `codex-obsidian-sync` 형태로 표기한다.

### 4. shell alias 사용

설치는 원하지 않지만 짧은 명령이 필요하면 alias를 둘 수 있다.
권장 순위는 `pip install -e .`보다 낮다.

예시:

```bash
alias codex-obsidian-sync='cd /path/to/codex-obsidian-sync && PYTHONPATH=src python3 -m codex_obsidian_sync.cli'
```

장점:

- 설치 없이 바로 쓸 수 있다
- 현재 repo 상태를 그대로 실행한다

단점:

- shell 설정 파일을 직접 관리해야 한다
- 다른 shell/session으로 옮기면 다시 잡아야 한다
- launchd의 `ProgramArguments`와는 별개라 운영 기준점이 둘로 갈릴 수 있다

## 사용 가능한 명령어

### `setup`

vault 경로와 cooldown을 저장하고 LaunchAgent plist를 준비한다.
시작은 하지 않는다.

```bash
codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m
```

옵션:

- `--vault`: Obsidian vault 절대 경로
- `--cooldown`: 백그라운드 확인 주기이자 최소 재실행 간격. `10s`, `1m`, `5m`, `30` 형태 지원

동작:

- config가 없으면 새 config를 만든다
- config가 있으면 현재 값을 보여주고 Enter로 유지할 수 있다
- 내부 저장은 계속 `interval_seconds` 정수다
- plist는 `~/Library/LaunchAgents/com.codex.obsidian-sync.plist`에 쓴다

### `start`

LaunchAgent를 시작한다.

```bash
codex-obsidian-sync start
```

동작:

- config가 있으면 prompt 없이 바로 시작한다
- config가 없으면 `vault -> cooldown` 순서로 물어 setup까지 수행한 뒤 바로 시작한다
- 이미 실행 중이면 idempotent하게 성공 처리한다

### `stop`

LaunchAgent를 중지한다.

```bash
codex-obsidian-sync stop
```

동작:

- LaunchAgent만 unload 한다
- `config.toml`, `sync-state.json`, `service-state.json`, 로그는 그대로 남는다

### `status`

현재 실행 상태와 최근 실행 결과를 보여준다.

```bash
codex-obsidian-sync status
```

JSON 출력:

```bash
codex-obsidian-sync status --json
```

표시 항목:

- config 경로
- vault 경로
- cooldown
- LaunchAgent loaded 여부
- pending 여부
- 다음 실행 예정 시각
- 마지막 실행 시각
- 마지막 성공 시각
- 마지막 에러 요약
- 마지막 처리 건수

### `service-run`

launchd가 내부적으로 호출하는 명령이다.
보통 사용자가 직접 칠 필요는 없지만, 문제를 분리해서 확인할 때는 유용하다.

```bash
codex-obsidian-sync service-run
```

용도:

- launchd 없이 실제 sync 경로만 한 번 태워보고 싶을 때
- `status`에 last success / last summary가 기록되는지 보고 싶을 때

### `sync-once`

launchd와 무관하게 즉시 한 번 동기화한다.

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault"
```

용도:

- launchd 설정 전 parser / writer 자체만 확인하고 싶을 때
- 특정 vault 대상으로 임시 실행하고 싶을 때

### `inspect-recent`

최근 rollout 후보를 요약해서 본다.

```bash
codex-obsidian-sync inspect-recent --limit 5
```

용도:

- 최근 세션이 어떤 식으로 보이는지 확인
- subagent 제외/포함이 맞는지 확인

### `inspect-rollout`

특정 rollout 파일 하나를 검사한다.

```bash
codex-obsidian-sync inspect-rollout /absolute/path/to/rollout.jsonl
```

용도:

- 문제 세션 하나만 떼어 보고 싶을 때
- title seed, project slug, message filtering 결과를 확인할 때

### `watch`

예전 polling 모드다.
현재 기본 UX는 `launchd + service-run`이므로 보통은 권장하지 않는다.

```bash
codex-obsidian-sync watch --vault "/absolute/path/to/vault" --interval 10
```

## 가장 자주 쓰는 흐름

### 최초 설정

```bash
codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m
codex-obsidian-sync start
codex-obsidian-sync status
```

권장 순서:

1. `pipx install -e .`
2. `pipx ensurepath`
3. 새 shell을 열거나 shell config를 다시 로드
4. `codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m`
5. `codex-obsidian-sync start`
6. `codex-obsidian-sync status`

### 설정 변경

```bash
codex-obsidian-sync setup
codex-obsidian-sync start
```

설정을 바꿨다면 `start`를 한 번 더 실행해 stale plist를 refresh 하는 흐름으로 생각하면 된다.

### 잠시 끄기

```bash
codex-obsidian-sync stop
```

다시 켜기:

```bash
codex-obsidian-sync start
```

### 바로 한 번 반영하기

```bash
codex-obsidian-sync service-run
```

또는:

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault"
```

## 저장 파일 위치

기본 경로:

- config: `~/.codex/obsidian-sync/config.toml`
- sync state: `~/.codex/obsidian-sync/sync-state.json`
- service state: `~/.codex/obsidian-sync/service-state.json`
- LaunchAgent plist: `~/Library/LaunchAgents/com.codex.obsidian-sync.plist`
- stdout log: `~/.codex/obsidian-sync/launchd.stdout.log`
- stderr log: `~/.codex/obsidian-sync/launchd.stderr.log`

## status 읽는 법

### `launchd_loaded`

- `true`: LaunchAgent가 등록되어 있고 trigger를 받을 준비가 됨
- `false`: 아직 `start`를 안 했거나 `stop`으로 내린 상태

### `pending`

- `yes`: cooldown 중 추가 `.codex` 활동이 들어와 한 번 더 돌 예정
- `no`: 대기 중

### `next_eligible_run`

- 미래 시각: cooldown이 끝나면 그 시각 이후에 실행 가능
- `none`: 예약된 추가 실행 없음
- `pending`: trigger는 왔지만 아직 eligible 시각 계산 전
- `ready now`: cooldown은 끝났고 새 trigger만 오면 바로 실행 가능

### `last_summary`

주요 필드:

- `processed`: note를 새로 쓰거나 갱신한 rollout 수
- `unchanged`: fingerprint/render hash 기준으로 건너뛴 rollout 수
- `skipped_subagents`: 기본 정책에 따라 제외한 subagent rollout 수
- `skipped_invalid`: malformed rollout 때문에 건너뛴 수
- `paused`: vault unavailable 때문에 pause한 경우
- `fast_path`: 변경 없음으로 빠르게 종료한 경우

## 트러블슈팅

### `status`에서 `vault`는 보이는데 `launchd_loaded=false`

`start`를 다시 실행한다.

```bash
codex-obsidian-sync start
```

### `launchd_loaded=true`인데 note가 안 생긴다

순서대로 본다.

1. `codex-obsidian-sync status`
2. `last_error` 확인
3. `codex-obsidian-sync service-run` 수동 실행
4. `status --json`에서 `last_summary`가 바뀌는지 확인

### 실제 trigger 없이 한 번만 확인하고 싶다

```bash
codex-obsidian-sync service-run
```

### launchd plist가 stale 같아 보인다

```bash
codex-obsidian-sync setup
codex-obsidian-sync start
```

### config를 직접 보고 싶다

```bash
cat ~/.codex/obsidian-sync/config.toml
```

## 참고

- source of truth는 계속 `.codex` 로그다
- `launchd`는 `cooldown` 간격마다 `service-run`을 실행하고, 실제 transcript 구성은 sync core가 한다
- `cooldown`은 백그라운드 확인 주기이자 최소 재실행 간격이다
- 각 실행 직후에는 rollout write가 안정될 때까지 아주 짧게 기다린 뒤 sync를 시작한다
- sync 중 source가 다시 바뀌면 follow-up pass를 한 번 더 예약한다
- `status`의 에러는 `type + one-line summary`만 저장한다
- `stop`은 정지가 목적이고 reset은 아니다

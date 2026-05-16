# codex-obsidian-sync Usage Guide

## 목적

`Codex CLI`와 `Codex Desktop`이 남기는 `~/.codex` 로그를 읽어,
Obsidian vault에 아래 노트를 자동으로 만든다.

- conversation note
- daily note
- project note

기본 실행 모델은 `macOS + launchd`다.
사용자는 한 번 `setup`만 해 두면, 이후 `Codex.app`와 `codex`를 평소처럼 사용하면 된다.
Rust migration build에서는 background write가 별도 config gate 뒤에 있다.

## 권장 설치 방식

릴리스가 공개된 뒤 일반 사용자는 GitHub Release의 macOS binary를 설치한다.

```bash
VERSION=v0.1.0 # replace with the current release tag
TARGET="$(uname -m)"
case "$TARGET" in
  arm64) TARGET=aarch64-apple-darwin ;;
  x86_64) TARGET=x86_64-apple-darwin ;;
  *) echo "unsupported architecture: $TARGET" >&2; exit 1 ;;
esac

BASE="https://github.com/HuiungJang/codex-obsidian-sync/releases/download/${VERSION}"
curl -LO "${BASE}/codex-obsidian-sync-${TARGET}.tar.gz"
curl -LO "${BASE}/codex-obsidian-sync-${TARGET}.tar.gz.sha256"
shasum -a 256 -c "codex-obsidian-sync-${TARGET}.tar.gz.sha256"
tar -xzf "codex-obsidian-sync-${TARGET}.tar.gz"
mkdir -p ~/.local/bin
install -m 0755 "codex-obsidian-sync-${TARGET}/codex-obsidian-sync" ~/.local/bin/codex-obsidian-sync
```

이후에는 어디서든 아래처럼 바로 실행할 수 있다.

```bash
codex-obsidian-sync status
```

릴리스 artifact는 architecture별 tarball과 SHA-256 checksum을 함께 제공한다.
release tag는 `Cargo.toml`의 Rust package version과 일치해야 한다.

Homebrew tap은 cutover release에서 공개한다.

```bash
brew tap HuiungJang/codex-obsidian-sync
brew install codex-obsidian-sync
```

## 대안 실행 방식

### 1. 로컬 Rust migration binary 사용

repo checkout 상태를 그대로 검증할 때는 Rust package를 local install한다.
이 경로의 binary 이름은 release artifact와 구분하기 위해 `codex-obsidian-sync-rs`다.

```bash
cd /path/to/codex-obsidian-sync
cargo install --path rust --locked
codex-obsidian-sync-rs status
```

설치 없이 바로 실행할 수도 있다.

```bash
cargo run --manifest-path rust/Cargo.toml -- status
```

### 2. Python legacy install

cutover 전 Python implementation을 확인해야 할 때만 `pipx`를 쓴다.

```bash
cd /path/to/codex-obsidian-sync
pipx install -e .
pipx ensurepath
```

Homebrew Python 환경에서는 `python3 -m pip install -e .`가 `externally-managed-environment`로 막힐 수 있으므로 Python 경로에서는 `pipx`를 선호한다.

### 3. Python venv 사용

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

### 4. Python repo에서 바로 실행

Python implementation을 설치하지 않고 module form으로 실행한다.

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

### 5. shell alias 사용

설치는 원하지 않지만 짧은 명령이 필요하면 alias를 둘 수 있다.
권장 순위는 release binary나 local Rust install보다 낮다.

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

## Cutover Monitoring

컷오버 직전에는 readiness audit을 먼저 실행한다. 이 명령은 release artifact, Homebrew formula,
설치될 Rust binary, Python rollback binary, 현재 LaunchAgent 상태, monitor directory 조건을
읽기 전용으로 점검하고 `ok=false`이면 컷오버하지 않는다.

```bash
python3 scripts/audit_cutover_readiness.py \
  --version <tag> \
  --release-dir dist \
  --homebrew-formula <tap>/Formula/codex-obsidian-sync.rb \
  --expected-rust-binary "$(command -v codex-obsidian-sync)" \
  --rollback-binary "$HOME/.local/bin/codex-obsidian-sync"
```

pre-cutover LaunchAgent binary까지 고정해서 확인하려면 현재 plist의 실제 `ProgramArguments[0]`를
`--expected-current-program-arg0`에 넘긴다.

Rust LaunchAgent cutover 직후에는 아래 checkpoint를 기록한다.

```bash
python3 scripts/record_cutover_monitor.py --checkpoint +5m --expected-program-arg0 "$(command -v codex-obsidian-sync)"
python3 scripts/record_cutover_monitor.py --checkpoint +1h --expected-program-arg0 "$(command -v codex-obsidian-sync)"
python3 scripts/record_cutover_monitor.py --checkpoint +4h --expected-program-arg0 "$(command -v codex-obsidian-sync)"
python3 scripts/record_cutover_monitor.py --checkpoint +24h --expected-program-arg0 "$(command -v codex-obsidian-sync)"
```

각 record는 `/tmp/codex-obsidian-sync-cutover-monitor` 아래에 남는다. `ok=false`이면 `no_go_reasons`를 먼저 확인한다.

### `service-run`

launchd가 내부적으로 호출하는 명령이다.
보통 사용자가 직접 칠 필요는 없지만, 문제를 분리해서 확인할 때는 유용하다.

```bash
codex-obsidian-sync service-run
```

용도:

- launchd 없이 실제 sync 경로만 한 번 태워보고 싶을 때
- `status`에 last success / last summary가 기록되는지 보고 싶을 때

Rust migration build에서는 background write 안전장치가 있다.
config에 `rust_service_write_enabled = true`가 없으면 `service-run`은 vault를 쓰지 않고 실패한다.
실제 LaunchAgent write를 켜기 전에는 copied-vault나 dry-run 검증을 먼저 끝낸다.

### `sync-once`

launchd와 무관하게 즉시 한 번 동기화한다.

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault"
```

기본값은 dry-run이다.
결과 파일을 남기려면 output directory를 지정한다.

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault" --dry-run-output /tmp/codex-obsidian-sync-dry-run
```

실제 vault와 state를 쓰려면 `--write`를 명시한다.

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault" --write
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

## Migration Safety Contract

Rust migration build의 기본 실행은 실제 vault를 쓰지 않는 dry-run이다.
다음 파일과 디렉터리는 `sync-once --write` 또는 gated `service-run` 전에는 수정하지 않는다.

- configured Obsidian vault
- `.codex` source logs and `session_index.jsonl`
- configured real `sync-state.json`
- configured lock files
- corrupt-state quarantine files

`--dry-run-output`은 real vault, `.codex`, state directory 내부를 거부하고 기존 non-empty directory도 거부한다.
출력 디렉터리에는 렌더링된 note와 temp `sync-state.json`이 남으므로, 삭제 전 diff 확인에 쓸 수 있다.

실제 쓰기 허용 조건:

- copied-vault `sync-once --write` 테스트가 통과해야 한다
- partial note/state failure recovery 테스트가 통과해야 한다
- LaunchAgent background write는 config에 `rust_service_write_enabled = true`가 있을 때만 가능하다
- public cutover 전에는 Python/pipx 실행 경로를 rollback path로 유지한다

## Python Rollback

cutover window 동안에는 Python implementation을 다시 설치하고 같은 config로 LaunchAgent를 되돌릴 수 있어야 한다.
rollback은 Rust LaunchAgent를 먼저 내린 뒤 Python command가 plist에 다시 기록되는지 확인하는 순서로 진행한다.

```bash
codex-obsidian-sync stop

cd /path/to/codex-obsidian-sync
pipx install -e . --force
pipx ensurepath

codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m
codex-obsidian-sync start
codex-obsidian-sync status --json
```

plist가 Python module invocation으로 돌아왔는지 확인한다.

```bash
python3 - <<'PY'
import plistlib
from pathlib import Path

plist = Path.home() / "Library/LaunchAgents/com.codex.obsidian-sync.plist"
args = plistlib.loads(plist.read_bytes())["ProgramArguments"]
print(args)
assert "-m" in args
assert "codex_obsidian_sync.cli" in args
assert "service-run" in args
PY
```

rollback 검증 기준:

- `launchctl print gui/$UID/com.codex.obsidian-sync`가 성공한다
- `codex-obsidian-sync status --json`이 JSON으로 parse된다
- `ProgramArguments`가 Rust binary path가 아니라 Python module invocation을 가리킨다
- rollback 직후 첫 실행 전에는 copied vault 또는 dry-run으로 note/state 차이를 확인한다

## 가장 자주 쓰는 흐름

### 최초 설정

```bash
codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m
codex-obsidian-sync start
codex-obsidian-sync status
```

권장 순서:

1. GitHub Release binary를 설치하거나 local testing용 `cargo install --path rust --locked`를 실행
2. `~/.local/bin` 또는 `~/.cargo/bin`이 `PATH`에 있는지 확인
3. `codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m`
4. `codex-obsidian-sync start`
5. `codex-obsidian-sync status`

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
codex-obsidian-sync sync-once --vault "/absolute/path/to/vault" --write
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
3. Rust migration build라면 `~/.codex/obsidian-sync/config.toml`에 `rust_service_write_enabled = true`가 있는지 확인
4. `codex-obsidian-sync service-run` 수동 실행
5. `status --json`에서 `last_summary`가 바뀌는지 확인

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

### Rust dry-run 결과를 Python과 비교하고 싶다

repo checkout에서 comparison harness를 실행한다.

```bash
cd /path/to/codex-obsidian-sync
/opt/homebrew/bin/python3.13 scripts/compare_python_rust_sync.py --output-dir /tmp/codex-obsidian-sync-compare
```

private fixture가 있으면 local fixture path를 명시한다.

```bash
/opt/homebrew/bin/python3.13 scripts/compare_python_rust_sync.py \
  --output-dir /tmp/codex-obsidian-sync-compare \
  --local-fixture /tmp/codex-obsidian-sync-phase14-anon-fixture
```

확인할 파일:

- `artifacts/summary.diff.json`
- `artifacts/notes.diff`
- `artifacts/state.diff.json`
- `artifacts/input_hashes.json`

### `skipped_invalid`가 늘어난다

`inspect-recent`로 최근 후보를 본 뒤, 문제 rollout 하나를 `inspect-rollout`으로 확인한다.
malformed rollout은 해당 rollout만 건너뛰고 전체 sync는 계속 성공해야 한다.

```bash
codex-obsidian-sync inspect-recent --limit 10
codex-obsidian-sync inspect-rollout /absolute/path/to/rollout.jsonl
```

### stale offset이 의심된다

offset은 source of truth가 아니라 성능 힌트다.
Rust sync는 stale offset이면 full rebuild로 복구해야 하며, 이때 `skipped_invalid`가 증가하면 안 된다.
의심될 때는 dry-run output을 새 directory에 만들고 `notes.diff`와 temp `sync-state.json`을 확인한다.

```bash
codex-obsidian-sync sync-once --dry-run-output /tmp/codex-obsidian-sync-stale-offset-check
```

## Release Checklist

release tag를 만들기 전:

- `cargo fmt --manifest-path rust/Cargo.toml --check`
- `cargo test --manifest-path rust/Cargo.toml`
- `cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings`
- `cargo build --release --manifest-path rust/Cargo.toml`
- signing/notarization decision 확인

GitHub Release:

- tag는 `v<Cargo.toml package version>` 형식이어야 한다
- `aarch64-apple-darwin` artifact와 checksum이 있어야 한다
- `x86_64-apple-darwin` artifact와 checksum이 있어야 한다
- release workflow는 checksum 검증 후 `codex-obsidian-sync.rb` Homebrew formula를 생성하고 Ruby 문법 검사를 통과해야 한다
- release workflow는 GitHub Release publish 후 생성된 formula로 Homebrew install/version/test/uninstall smoke를 통과해야 한다
- downloaded `.tar.gz`는 같은 directory의 `.sha256`으로 `shasum -a 256 -c`가 통과해야 한다
- installed binary는 `codex-obsidian-sync --version`으로 release version을 보고해야 한다
- `python3 scripts/smoke_release_artifact.py --tarball <artifact>.tar.gz --checksum <artifact>.tar.gz.sha256 --expected-version <version>`이 통과해야 한다

Homebrew cutover:

- formula URL은 GitHub Release tarball을 가리켜야 한다
- formula checksum은 published `.sha256`과 일치해야 한다
- `python3 scripts/generate_homebrew_formula.py --version <tag> --aarch64-checksum <aarch64>.sha256 --x86-64-checksum <x86_64>.sha256 --output <tap>/Formula/codex-obsidian-sync.rb`로 formula를 생성한다
- `python3 scripts/smoke_homebrew_formula.py --formula <tap>/Formula/codex-obsidian-sync.rb --expected-version <tag>`가 `brew install`, `codex-obsidian-sync --version`, `brew test`, `brew uninstall` smoke를 통과해야 한다
- cutover window 동안 Python/pipx rollback path를 유지한다

## 참고

- source of truth는 계속 `.codex` 로그다
- `launchd`는 `cooldown` 간격마다 `service-run`을 실행하고, 실제 transcript 구성은 sync core가 한다
- `cooldown`은 백그라운드 확인 주기이자 최소 재실행 간격이다
- 각 실행 직후에는 rollout write가 안정될 때까지 아주 짧게 기다린 뒤 sync를 시작한다
- sync 중 source가 다시 바뀌면 follow-up pass를 한 번 더 예약한다
- `status`의 에러는 `type + one-line summary`만 저장한다
- `stop`은 정지가 목적이고 reset은 아니다

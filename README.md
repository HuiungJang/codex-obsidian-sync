# codex-obsidian-sync

`Codex CLI`와 `Codex Desktop`이 남기는 `~/.codex` JSONL 로그를 읽어
Obsidian vault에 conversation / daily / project note를 생성한다.

현재 기본 실행 UX는 `macOS + launchd` 기준이다.
사용자는 `vault`와 `cooldown`만 설정하고, 이후에는 `Codex.app`와 `codex`를 그대로 쓰면 된다.
`launchd`가 `cooldown` 간격마다 `service-run`을 깨우고, sync core가 바뀐 내용이 있을 때만 실제 note를 갱신한다.

상세 사용 가이드는 [USAGE.md](USAGE.md) 를 본다.

## Quick Start

권장 설치:

```bash
cd /path/to/codex-obsidian-sync
pipx install -e .
pipx ensurepath
```

Homebrew Python 환경에서는 `python3 -m pip install -e .`가 `externally-managed-environment`로 막힐 수 있다.
이 도구는 Python application이라 `pipx`가 더 맞다.

최초 설정:

```bash
codex-obsidian-sync setup --vault "/absolute/path/to/your/obsidian-vault" --cooldown 1m
```

자동 동기화 시작:

```bash
codex-obsidian-sync start
```

상태 확인:

```bash
codex-obsidian-sync status
```

중지:

```bash
codex-obsidian-sync stop
```

동작 방식:

- `setup`은 config와 LaunchAgent plist를 준비만 한다
- `start`는 LaunchAgent를 load한다
- `stop`은 LaunchAgent만 unload하고 config/state/log는 유지한다
- LaunchAgent는 `cooldown` 초마다 `service-run`을 실행한다
- `status`는 config, loaded 상태, pending, 다음 eligible 실행 시각, 마지막 성공/실패 요약을 보여준다

## Commands

### `setup`

`vault`와 `cooldown`을 저장하고 plist를 생성한다. 시작은 하지 않는다.

```bash
codex-obsidian-sync setup --vault "/absolute/path/to/vault" --cooldown 1m
```

기존 config가 있으면 현재 값을 보여주고, Enter로 유지할 수 있다.

지원하는 cooldown 예시:

- `10s`
- `1m`
- `5m`
- `30`

내부 저장은 항상 초 단위 정수다.

### `start`

LaunchAgent를 시작한다.

```bash
codex-obsidian-sync start
```

config가 없으면 `vault -> cooldown` 순서로 물어 setup까지 끝낸 뒤 바로 시작한다.

### `stop`

LaunchAgent를 중지한다.

```bash
codex-obsidian-sync stop
```

### `status`

현재 실행 상태와 최근 실행 결과를 보여준다.

```bash
codex-obsidian-sync status
```

JSON 출력:

```bash
codex-obsidian-sync status --json
```

## Files

기본 경로:

- config: `~/.codex/obsidian-sync/config.toml`
- sync state: `~/.codex/obsidian-sync/sync-state.json`
- service state: `~/.codex/obsidian-sync/service-state.json`
- LaunchAgent plist: `~/Library/LaunchAgents/com.codex.obsidian-sync.plist`

기본적으로 아래를 읽는다:

- `~/.codex/sessions`
- `~/.codex/sessions/**/*.jsonl`

## Notes

- source of truth는 계속 `.codex` 로그다
- `launchd`는 `cooldown` 간격마다 `service-run`을 실행하고, 실제 transcript 구성은 기존 sync parser가 담당한다
- `cooldown`은 백그라운드 확인 주기이면서 연속 실행의 최소 간격이다
- 각 실행 직후에는 rollout append가 마무리될 시간을 짧게 기다린 뒤 sync를 시작한다
- cooldown 중 수동 실행이나 추가 후속 pass가 필요하면 `pending=true`로 기록하고, eligible 시점에 한 번 더 sync한다
- sync 중 source가 다시 바뀌면 외부 trigger가 없어도 follow-up pass를 한 번 더 예약한다
- `status`의 마지막 에러는 `type + one-line summary`만 저장한다
- content-free structured logging, fail-closed invalid rollout skip, subagent 제외 기본값 같은 기존 안전 규칙은 유지한다

## Advanced / Debug

최근 rollout file 요약:

```bash
codex-obsidian-sync inspect-recent --limit 5
```

특정 rollout file 검사:

```bash
codex-obsidian-sync inspect-rollout /absolute/path/to/rollout.jsonl
```

실제 vault로 한 번 동기화:

```bash
codex-obsidian-sync sync-once --vault "/absolute/path/to/your/obsidian-vault"
```

기존 polling watch 모드:

```bash
codex-obsidian-sync watch --vault "/absolute/path/to/your/obsidian-vault" --interval 10
```

테스트:

```bash
cd /path/to/codex-obsidian-sync
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

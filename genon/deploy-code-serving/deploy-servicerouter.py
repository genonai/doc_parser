#!/usr/bin/env python3
"""코드서빙 배포 + 서비스 라우터 규칙 기반 라우팅 전환.

deploy.py를 import하지 않는 독립 실행 스크립트이며 config.yaml.serving만 사용한다.

기본 흐름:
  1. 지정 브랜치/커밋으로 새 코드서빙 리비전 배포 및 APP_RUNNING 확인
  2. 서비스 라우터 target에 새 리비전 추가
  3. ROUTING_STRATEGY에 새 리비전 weight=1 등록
  4. 선택 환경 인증키의 ROUTING_RULE route_to를 새 리비전으로 전환
  5. 코드서빙 direct endpoint weight를 모두 0으로 정리
  6. 어떤 활성 ROUTING_RULE도 참조하지 않는 이전 target 제거 및 리비전 중지

사용 예:
  uv run python deploy-servicerouter.py --env dev --branch develop
  uv run python deploy-servicerouter.py --env dev --branch feature/foo --commit abc1234

  # 새 리비전 배포와 라우팅 전환을 분리하려는 경우
  uv run python deploy-servicerouter.py --env dev --branch feature/foo --deploy-only
  uv run python deploy-servicerouter.py --env dev --route-only --revision 321

--env 기본값은 dev다. GenOS 접속·환경별 배포·env·라우터 설정을 독립 설정 파일인
config.yaml.serving에서 모두 읽는다.
"""

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml


DEFAULT_CONFIG = Path(__file__).parent / "config.yaml.serving"
_REVISION_LIST_PAGE_SIZE = 10_000
_CODE_SERVING_RESOURCE_TYPE = "RT0505"
_CODE_SERVING_MODEL_TYPE = "MT0000"
_STATUS_APP_RUNNING = "SS0302"
_STATUS_ERRORS = {"SS0204", "SS0107", "SS0110", "SS0111"}
_REVISION_STATUS_STOPPED = "SS0205"
_LIST_POLICY_TYPES = {"ROUTING_RULE", "RATE_LIMIT"}


class GenosApiError(RuntimeError):
    """HTTP 200으로 전달되는 GenOS 비즈니스 오류."""

    def __init__(self, code: Any, message: str, body: dict | None = None):
        self.code = code
        self.message = message
        self.body = body or {}
        super().__init__(f"GenOS API 오류({code}): {message}")


class DeploymentProgress:
    """단일 코드서빙 배포의 단계·퍼센트·컨테이너 상태를 표시한다."""

    def __init__(self, code_serving_id: int):
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
        )

        self._progress = Progress(
            SpinnerColumn(finished_text="[green]✓[/green]"),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            TextColumn("[dim]rev={task.fields[revision]}[/dim]"),
            BarColumn(bar_width=28),
            TaskProgressColumn(),
            TextColumn("[bold]{task.fields[stage]}[/bold]"),
            TextColumn("[dim]{task.fields[status]}[/dim]"),
            refresh_per_second=8,
        )
        self._task_id = self._progress.add_task(
            f"code-serving-{code_serving_id}",
            total=100,
            completed=0,
            revision="-",
            stage="배포 준비",
            status="",
        )
        self._started = False

    def __enter__(self) -> "DeploymentProgress":
        self._progress.start()
        self._started = True
        return self

    def __exit__(self, *args) -> None:
        if self._started:
            self._progress.stop()
            self._started = False

    def update_stage(
        self,
        stage: str,
        completed: float,
        revision_id: int | None = None,
        status: str = "",
    ) -> None:
        fields: dict[str, Any] = {"stage": stage, "status": status}
        if revision_id is not None:
            fields["revision"] = str(revision_id)
        self._progress.update(
            self._task_id,
            completed=max(0, min(100, completed)),
            **fields,
        )

    def update_poll(self, pod_status: str, remaining: int, timeout: int) -> None:
        elapsed_ratio = 1 - (max(0, remaining) / max(1, timeout))
        completed = 75 + (24 * max(0, min(1, elapsed_ratio)))
        status = f"{pod_status or '상태 확인 중'} · {remaining}s 남음"
        self.update_stage("컨테이너 대기", completed, status=status)

    def mark_done(self, revision_id: int) -> None:
        self.update_stage(
            "배포 완료",
            100,
            revision_id=revision_id,
            status="APP_RUNNING",
        )

    def mark_failed(self, message: str) -> None:
        task = self._progress.tasks[self._task_id]
        self.update_stage(
            "[red]배포 실패[/red]",
            task.completed,
            status=message[:80],
        )


class CheckedGenosClient:
    """서비스 라우터 배포에 필요한 GenOS Admin API 독립 클라이언트."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        dry_run: bool = False,
        debug: bool = False,
        quiet: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.dry_run = dry_run
        self.debug = debug
        self.quiet = quiet
        self._token: str | None = None
        self._session = requests.Session()
        parsed = urlparse(self.base_url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._session.hooks.setdefault("response", []).append(self._check_response_hook)

    @staticmethod
    def _check_response_hook(response, *args, **kwargs):
        response.raise_for_status()
        if not response.content:
            return response
        try:
            body = response.json()
        except ValueError:
            return response
        if isinstance(body, dict) and body.get("code") not in (None, 0, "0"):
            code = body.get("error_code") or body.get("code")
            message = body.get("errMsg") or body.get("message") or str(body)
            raise GenosApiError(code, str(message), body)
        return response

    def _log(self, message: str) -> None:
        if not self.quiet:
            print(message)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _response_data(response, context: str):
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{context} 응답이 JSON이 아닙니다.") from exc
        if not isinstance(body, dict) or "data" not in body:
            raise RuntimeError(f"{context} 응답에 data가 없습니다: {body}")
        return body["data"]

    def fork(self) -> "CheckedGenosClient":
        client = CheckedGenosClient(
            self.base_url,
            self.username,
            self.password,
            self.dry_run,
            self.debug,
            self.quiet,
        )
        client._token = self._token
        return client

    def login(self) -> None:
        response = self._session.post(
            f"{self.base_url}/auth/login",
            json={"user_id": self.username, "password": self.password},
        )
        data = self._response_data(response, "GenOS 로그인")
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise RuntimeError("GenOS 로그인 응답에 access_token이 없습니다.")
        self._token = token
        self._log("  ✓ GenOS 로그인 완료")

    def _fetch_commits(
        self,
        code_serving_id: int,
        pg_size: int = 1,
        branch: str | None = None,
    ) -> list[dict]:
        params: dict = {"pgSize": pg_size}
        if branch:
            params["branch"] = branch
        response = self._session.get(
            f"{self.base_url}/serving/code/{code_serving_id}/commits",
            headers=self._headers(),
            params=params,
        )
        data = self._response_data(response, "커밋 목록 조회")
        commits = data.get("list") if isinstance(data, dict) else None
        if not commits:
            suffix = f" (branch={branch})" if branch else ""
            raise RuntimeError(f"커밋 목록이 비어 있습니다{suffix}.")
        return commits

    def get_latest_commit(
        self,
        code_serving_id: int,
        branch: str | None = None,
    ) -> str:
        commits = self._fetch_commits(code_serving_id, pg_size=1, branch=branch)
        return commits[0]["commit_hash"]

    def validate_commit(
        self,
        code_serving_id: int,
        commit_hash: str,
        recent: int = 30,
        branch: str | None = None,
    ) -> None:
        commits = self._fetch_commits(code_serving_id, pg_size=recent, branch=branch)
        if not any(item["commit_hash"].startswith(commit_hash) for item in commits):
            recent_hashes = [item["commit_hash"][:8] for item in commits]
            raise ValueError(
                f"커밋 {commit_hash!r}이 최근 {recent}개 커밋에 없습니다. "
                f"최근 커밋: {recent_hashes}"
            )
        self._log(f"  ✓ 커밋 검증 완료: {commit_hash[:8]}")

    def list_revisions(
        self,
        code_serving_id: int,
        pg_size: int = 500,
    ) -> list[dict]:
        response = self._session.get(
            f"{self.base_url}/serving/code/{code_serving_id}/revisions",
            headers=self._headers(),
            params={"pgSize": pg_size},
        )
        data = self._response_data(response, "리비전 목록 조회")
        revisions = data.get("list") if isinstance(data, dict) else None
        if not isinstance(revisions, list):
            raise RuntimeError(f"리비전 목록 응답 형식이 올바르지 않습니다: {data}")
        return revisions

    def create_revision(
        self,
        code_serving_id: int,
        commit_hash: str,
        docker_image_id: int,
        instance_type_id: int,
        replicas: int,
    ) -> int:
        payload = {
            "commit_hash": commit_hash,
            "docker_image_id": docker_image_id,
            "instance_type_id": instance_type_id,
            "replicas": replicas,
        }
        if self.dry_run:
            self._log(
                f"  [dry-run] POST /serving/code/{code_serving_id}/revisions {payload}"
            )
            return -1
        response = self._session.post(
            f"{self.base_url}/serving/code/{code_serving_id}/revisions",
            headers=self._headers(),
            json=payload,
        )
        data = self._response_data(response, "리비전 생성")
        revision_id = (
            data.get("code_serving_revision_id") if isinstance(data, dict) else None
        )
        if revision_id is None:
            raise RuntimeError(f"리비전 생성 응답에 revision_id가 없습니다: {data}")
        self._log(f"  ✓ 리비전 생성: revision_id={revision_id}")
        return int(revision_id)

    def set_revision_commands(
        self,
        code_serving_id: int,
        revision_id: int,
        build_command: str,
        start_command: str,
        envs: list[dict],
    ) -> None:
        """리비전 명령과 env를 저장하고 dry-run에서는 secret 값을 마스킹한다."""
        request_envs = [
            {
                "key": item["key"],
                "value": item.get("value", ""),
                "is_secret": item.get("is_secret", False),
                "is_changed": True,
            }
            for item in envs
        ]
        payload = {
            "build_command": build_command,
            "start_command": start_command,
            "envs": request_envs,
        }
        if self.dry_run:
            safe_payload = {
                **payload,
                "envs": [
                    {
                        **item,
                        "value": "***" if item["is_secret"] else item["value"],
                    }
                    for item in request_envs
                ],
            }
            self._log(
                f"  [dry-run] POST /serving/code/{code_serving_id}/revisions/"
                f"{revision_id}/commands {safe_payload}"
            )
            return
        self._session.post(
            f"{self.base_url}/serving/code/{code_serving_id}/revisions/"
            f"{revision_id}/commands",
            headers=self._headers(),
            json=payload,
        )
        self._log("  ✓ 커맨드/환경변수 설정 완료")

    def deploy_revision(
        self,
        code_serving_id: int,
        revision_id: int,
    ) -> tuple[int, int]:
        if self.dry_run:
            self._log(
                f"  [dry-run] POST /serving/code/{code_serving_id}/revisions/"
                f"{revision_id}/deploy"
            )
            return -1, -1
        response = self._session.post(
            f"{self.base_url}/serving/code/{code_serving_id}/revisions/"
            f"{revision_id}/deploy",
            headers=self._headers(),
        )
        data = self._response_data(response, "리비전 배포 요청")
        if not isinstance(data, dict):
            raise RuntimeError(f"리비전 배포 응답 형식이 올바르지 않습니다: {data}")
        approval_id = data.get("approval_id")
        deployment_id = data.get("code_serving_deployment_id")
        if approval_id is None or deployment_id is None:
            raise RuntimeError(f"리비전 배포 승인 정보가 없습니다: {data}")
        self._log(
            f"  ✓ 배포 요청 완료: approval_id={approval_id}, "
            f"deployment_id={deployment_id}"
        )
        return int(approval_id), int(deployment_id)

    def approve_deployment(
        self,
        approval_id: int,
        deployment_id: int,
        code_serving_id: int,
    ) -> None:
        payload = {
            "id": approval_id,
            "resource_type": "CODE_SERVING",
            "resource_id": deployment_id,
            "resource_parents_id": code_serving_id,
        }
        if self.dry_run:
            self._log(
                "  [dry-run] POST /approval/approve "
                f"{{id={approval_id}, resource_type=CODE_SERVING}}"
            )
            return
        self._session.post(
            f"{self.base_url}/approval/approve",
            headers=self._headers(),
            json=payload,
        )
        self._log("  ✓ 배포 승인 완료")

    def wait_for_container_ready(
        self,
        code_serving_id: int,
        revision_id: int,
        poll_interval: int,
        timeout: int,
        on_poll=None,
    ) -> None:
        container_name = f"code-serving-{code_serving_id}-{revision_id}"
        url = f"{self._origin}/api/container-status/v1/status/current"
        payload = {"container_name": [container_name], "disableErrorToast": True}
        if on_poll is None:
            self._log(
                f"  ⏳ 컨테이너 준비 대기 중 (최대 {timeout}초)... [{container_name}]"
            )
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self._session.post(
                url,
                headers=self._headers(),
                json=payload,
            )
            statuses = response.json()
            remaining = max(0, int(deadline - time.time()))
            if not isinstance(statuses, list) or not statuses:
                if on_poll:
                    on_poll("", remaining)
                else:
                    self._log(f"    컨테이너 상태 없음 — {remaining}초 남음")
                time.sleep(poll_interval)
                continue
            entry = statuses[0]
            status = entry.get("status") or entry.get("pod_status")
            if self.debug:
                self._log(
                    "    [debug] container_status="
                    f"{json.dumps(entry, ensure_ascii=False)}"
                )
            if status == _STATUS_APP_RUNNING:
                if on_poll:
                    on_poll(status, 0)
                self._log("  ✓ 컨테이너 준비 완료 (APP_RUNNING)")
                return
            if status in _STATUS_ERRORS:
                raise RuntimeError(
                    f"컨테이너 오류: status={status}, reason={entry.get('reason')}"
                )
            if on_poll:
                on_poll(status or "", remaining)
            else:
                self._log(f"    상태: {status} — {remaining}초 남음")
            time.sleep(poll_interval)
        raise TimeoutError(f"컨테이너 준비 타임아웃: {container_name}")

    def upsert_routing_strategy_weights(
        self,
        router_id: int,
        new_revision_ids: list[int],
        existing_policies: list[dict],
        weight: int = 1,
    ) -> None:
        strategy_policy = next(
            (
                policy
                for policy in existing_policies
                if policy.get("policy_type") == "ROUTING_STRATEGY"
                and policy.get("enabled", True)
            ),
            None,
        )
        if strategy_policy is None:
            cfg = {"strategy": "WEIGHT", "representative_revisions": []}
            priority = 0
        else:
            cfg = dict(strategy_policy.get("config_json") or {})
            priority = strategy_policy.get("priority", 0)
        representatives = list(cfg.get("representative_revisions") or [])
        for revision_id in new_revision_ids:
            representative = next(
                (
                    item
                    for item in representatives
                    if item.get("revision_id") is not None
                    and int(item["revision_id"]) == revision_id
                ),
                None,
            )
            if representative is None:
                representatives.append({"revision_id": revision_id, "weight": weight})
            else:
                representative["weight"] = weight
        cfg["representative_revisions"] = representatives
        payload = {
            "policy_type": "ROUTING_STRATEGY",
            "config_json": cfg,
            "enabled": True,
            "priority": priority,
        }
        if self.dry_run:
            self._log(f"  [dry-run] POST /service-router/{router_id}/policies {payload}")
            return
        self._session.post(
            f"{self.base_url}/service-router/{router_id}/policies",
            headers=self._headers(),
            json=payload,
        )
        self._log(
            f"  ✓ ROUTING_STRATEGY 설정: revision_ids={new_revision_ids}, weight={weight}"
        )

    def get_targets(self, router_id: int) -> list[dict]:
        response = self._session.get(
            f"{self.base_url}/service-router/{router_id}/targets",
            headers=self._headers(),
        )
        data = self._response_data(response, "서비스 라우터 target 조회")
        if not isinstance(data, list):
            raise RuntimeError(f"서비스 라우터 target 응답 형식 오류: {data}")
        return data

    def replace_targets(self, router_id: int, targets: list[dict]) -> None:
        payload = {"targets": targets}
        if self.dry_run:
            self._log(f"  [dry-run] POST /service-router/{router_id}/targets {payload}")
            return
        self._session.post(
            f"{self.base_url}/service-router/{router_id}/targets",
            headers=self._headers(),
            json=payload,
        )
        self._log(f"  ✓ 서비스 라우터 target 업데이트 완료 ({len(targets)}개)")

    def get_policies(self, router_id: int) -> list[dict]:
        response = self._session.get(
            f"{self.base_url}/service-router/{router_id}/policies",
            headers=self._headers(),
        )
        data = self._response_data(response, "서비스 라우터 정책 조회")
        policies = data.get("policies") if isinstance(data, dict) else None
        if not isinstance(policies, list):
            raise RuntimeError(f"서비스 라우터 정책 응답 형식 오류: {data}")
        if self.debug:
            for policy in policies:
                self._log(
                    f"  [debug] policy_type={policy.get('policy_type')} "
                    f"priority={policy.get('priority')} "
                    f"config_json={json.dumps(policy.get('config_json'), ensure_ascii=False)}"
                )
        return policies

    def batch_update_routing_rules(
        self,
        router_id: int,
        updates: dict[int, int],
        existing_policies: list[dict],
    ) -> None:
        updated_policies: list[dict] = []
        matched_keys: set[int] = set()
        for policy in existing_policies:
            policy_type = policy.get("policy_type")
            if policy_type not in _LIST_POLICY_TYPES:
                continue
            if policy_type != "ROUTING_RULE":
                updated_policies.append({
                    "policy_type": policy_type,
                    "config_json": policy.get("config_json"),
                    "enabled": policy.get("enabled", True),
                    "priority": policy.get("priority", 0),
                })
                continue
            cfg = policy.get("config_json") or {}
            when = (cfg.get("when") or {}) if isinstance(cfg, dict) else {}
            key_id = when.get("auth_key_id") if isinstance(when, dict) else None
            if isinstance(key_id, dict):
                key_id = key_id.get("value")
            try:
                key_id = int(key_id) if key_id is not None else None
            except (TypeError, ValueError):
                key_id = None
            next_cfg = {**cfg, "route_to": updates[key_id]} if key_id in updates else cfg
            if key_id in updates:
                matched_keys.add(key_id)
            updated_policies.append({
                "policy_type": "ROUTING_RULE",
                "config_json": next_cfg,
                "enabled": policy.get("enabled", True),
                "priority": policy.get("priority", 0),
            })
        new_keys = set(updates) - matched_keys
        max_priority = max(
            (
                int(policy.get("priority", 0))
                for policy in existing_policies
                if policy.get("policy_type") == "ROUTING_RULE"
            ),
            default=-1,
        )
        for offset, key_id in enumerate(sorted(new_keys), start=1):
            updated_policies.append({
                "policy_type": "ROUTING_RULE",
                "config_json": {
                    "when": {"auth_key_id": key_id},
                    "route_to": updates[key_id],
                    "action": "route",
                },
                "enabled": True,
                "priority": max_priority + offset,
            })
        payload = {"policies": updated_policies}
        if self.dry_run:
            self._log(
                f"  [dry-run] POST /service-router/{router_id}/policies/batch {payload}"
            )
            return
        self._session.post(
            f"{self.base_url}/service-router/{router_id}/policies/batch",
            headers=self._headers(),
            json=payload,
        )
        for key_id, revision_id in updates.items():
            action = "업데이트" if key_id in matched_keys else "신규 추가"
            self._log(
                f"  ✓ ROUTING_RULE {action}: auth_key_id={key_id} → {revision_id}"
            )

    def remove_from_routing_strategy(
        self,
        router_id: int,
        remove_revision_ids: set[int],
        existing_policies: list[dict],
    ) -> None:
        strategy_policy = next(
            (
                policy
                for policy in existing_policies
                if policy.get("policy_type") == "ROUTING_STRATEGY"
                and policy.get("enabled", True)
            ),
            None,
        )
        if strategy_policy is None:
            return
        cfg = dict(strategy_policy.get("config_json") or {})
        before = cfg.get("representative_revisions") or []
        after = [
            item
            for item in before
            if item.get("revision_id") is None
            or int(item["revision_id"]) not in remove_revision_ids
        ]
        if len(before) == len(after):
            return
        cfg["representative_revisions"] = after
        payload = {
            "policy_type": "ROUTING_STRATEGY",
            "config_json": cfg,
            "enabled": True,
            "priority": strategy_policy.get("priority", 0),
        }
        if self.dry_run:
            self._log(f"  [dry-run] POST /service-router/{router_id}/policies {payload}")
            return
        self._session.post(
            f"{self.base_url}/service-router/{router_id}/policies",
            headers=self._headers(),
            json=payload,
        )
        self._log(f"  ✓ ROUTING_STRATEGY에서 이전 리비전 제거: {remove_revision_ids}")

    def stop_revision(self, code_serving_id: int, revision_id: int) -> None:
        """비즈니스 오류를 성공으로 오인하지 않는 리비전 중지 요청·승인."""
        if self.dry_run:
            self._log(
                f"  [dry-run] POST /serving/code/{code_serving_id}/revisions/"
                f"{revision_id}/stop-request"
            )
            self._log(
                "  [dry-run] POST /approval/approve "
                f"{{resource_type=CODE_SERVING, revision_id={revision_id}}}"
            )
            return

        try:
            response = self._session.post(
                f"{self.base_url}/serving/code/{code_serving_id}/revisions/"
                f"{revision_id}/stop-request",
                headers=self._headers(),
            )
        except GenosApiError as exc:
            # 활성 deployment가 없으면 내릴 컨테이너도 없으므로 정리 완료로 취급한다.
            if str(exc.code) == "05050015":
                self._log(f"  ✓ 리비전 {revision_id}: 활성 deployment 없음(중지 불필요)")
                return
            raise

        body = response.json() if response.content else {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or data.get("approval_id") is None:
            self._log(f"  ✓ 리비전 {revision_id}: 중지 승인 불필요(이미 중지 중)")
            return

        approval_id = data["approval_id"]
        deployment_id = data["code_serving_deployment_id"]
        self._log(f"  ✓ 중지 요청: revision_id={revision_id}, approval_id={approval_id}")

        self._session.post(
            f"{self.base_url}/approval/approve",
            headers=self._headers(),
            json={
                "id": approval_id,
                "resource_type": "CODE_SERVING",
                "resource_id": deployment_id,
                "resource_parents_id": code_serving_id,
            },
        )
        self._log(f"  ✓ 리비전 중지 승인: revision_id={revision_id}")


def deploy_code_serving(
    client: CheckedGenosClient,
    deployment_cfg: dict,
    code_serving_id: int,
    commit_hash: str,
    envs: list[dict],
    cleanup_on_failure: bool = True,
    progress: DeploymentProgress | None = None,
) -> int:
    """리비전 생성부터 APP_RUNNING 확인까지 단일 코드서빙 배포를 수행한다."""
    revision_id: int | None = None

    def step(label: str, completed: float) -> None:
        if progress:
            progress.update_stage(
                label,
                completed,
                revision_id=revision_id,
            )
            return
        print(f"\n{'='*60}")
        print(
            f"  [코드서빙 배포] code-serving-{code_serving_id} | "
            f"commit: {commit_hash[:8]}"
        )
        print(f"  → {label}")
        print(f"{'='*60}")

    try:
        step("리비전 생성", 5)
        revision_id = client.create_revision(
            code_serving_id=code_serving_id,
            commit_hash=commit_hash,
            docker_image_id=int(deployment_cfg["docker_image_id"]),
            instance_type_id=int(deployment_cfg["instance_type_id"]),
            replicas=int(deployment_cfg["replicas"]),
        )

        step("커맨드 및 환경변수 설정", 25)
        client.set_revision_commands(
            code_serving_id=code_serving_id,
            revision_id=revision_id,
            build_command=deployment_cfg.get("build_command", ""),
            start_command=deployment_cfg.get("start_command", ""),
            envs=envs,
        )

        step("배포 요청", 45)
        approval_id, deployment_id = client.deploy_revision(
            code_serving_id=code_serving_id,
            revision_id=revision_id,
        )

        step("배포 승인", 60)
        client.approve_deployment(
            approval_id=approval_id,
            deployment_id=deployment_id,
            code_serving_id=code_serving_id,
        )

        if not client.dry_run:
            step("컨테이너 APP_RUNNING 대기", 75)
            client.wait_for_container_ready(
                code_serving_id=code_serving_id,
                revision_id=revision_id,
                poll_interval=int(deployment_cfg["poll_interval_sec"]),
                timeout=int(deployment_cfg["poll_timeout_sec"]),
                on_poll=(
                    lambda status, remaining: progress.update_poll(
                        status,
                        remaining,
                        int(deployment_cfg["poll_timeout_sec"]),
                    )
                )
                if progress
                else None,
            )
        if progress:
            progress.mark_done(revision_id)
        return revision_id
    except Exception as error:
        if progress:
            progress.mark_failed(str(error))
        if cleanup_on_failure and revision_id is not None:
            print(
                f"  [WARN] 실패 리비전 자동 중지 시도: revision_id={revision_id}",
                file=sys.stderr,
            )
            try:
                client.stop_revision(code_serving_id, revision_id)
            except Exception as cleanup_error:
                print(
                    "  [WARN] 실패 리비전 자동 중지 실패: "
                    f"revision_id={revision_id}, error={cleanup_error}",
                    file=sys.stderr,
                )
        raise


def load_service_config(path: str) -> dict:
    """독립 config.yaml.serving을 읽고 환경변수 override를 적용한다."""
    service_path = Path(path).expanduser().resolve()
    with service_path.open(encoding="utf-8") as file:
        cfg = yaml.safe_load(file) or {}

    if "base_config" in cfg:
        raise ValueError(
            "config.yaml.serving은 독립 설정 파일이어야 하므로 base_config를 사용할 수 없습니다."
        )

    if password := os.environ.get("GENOS_PASSWORD"):
        cfg["genos"]["password"] = password
    for env_item in cfg.get("envs") or []:
        key = env_item["key"]
        if (value := os.environ.get(key)) is not None:
            env_item["value"] = value

    required = ("genos", "deployment", "code_serving_id", "service_router_id", "auth_key_id")
    missing = [key for key in required if key not in cfg]
    if missing:
        raise ValueError(f"서비스 라우터 설정 누락: {missing}")
    deployment_required = (
        "docker_image_id",
        "instance_type_id",
        "replicas",
        "poll_interval_sec",
        "poll_timeout_sec",
        "start_command",
    )
    for env_name in ("dev", "prod"):
        if not (cfg.get("auth_key_id") or {}).get(env_name):
            raise ValueError(f"auth_key_id.{env_name} 설정이 필요합니다.")
        deployment_cfg = (cfg.get("deployment") or {}).get(env_name)
        if not isinstance(deployment_cfg, dict):
            raise ValueError(f"deployment.{env_name} 설정이 필요합니다.")
        deployment_missing = [
            key for key in deployment_required if key not in deployment_cfg
        ]
        if deployment_missing:
            raise ValueError(
                f"deployment.{env_name} 설정 누락: {deployment_missing}"
            )
    return cfg


def _target_payload(target: dict) -> dict:
    return {
        "resource_type": target["resource_type"],
        "resource_id": target["resource_id"],
        "resource_revision_id": target.get("resource_revision_id"),
        "model_type": target["model_type"],
    }


def _policy_auth_key_id(policy: dict) -> int | None:
    config = policy.get("config_json") or {}
    when = (config.get("when") or {}) if isinstance(config, dict) else {}
    key_id = when.get("auth_key_id") if isinstance(when, dict) else None
    if isinstance(key_id, dict):
        key_id = key_id.get("value")
    try:
        return int(key_id) if key_id is not None else None
    except (TypeError, ValueError):
        return None


def _policy_route_to(policy: dict) -> int | None:
    config = policy.get("config_json") or {}
    route_to = config.get("route_to") if isinstance(config, dict) else None
    try:
        return int(route_to) if route_to is not None else None
    except (TypeError, ValueError):
        return None


def update_and_verify_service_router(
    client: CheckedGenosClient,
    code_serving_id: int,
    service_router_id: int,
    auth_key_id: int,
    revision_id: int,
) -> None:
    """새 리비전을 target/strategy/rule에 순서대로 등록하고 결과를 재검증한다."""
    print(f"\n{'='*60}")
    print("  [서비스 라우터 전환] target → weight → ROUTING_RULE")
    print(f"{'='*60}")

    if client.dry_run:
        print(
            "  [dry-run] target 추가: "
            f"router={service_router_id}, code_serving={code_serving_id}, revision={revision_id}"
        )
        print(f"  [dry-run] ROUTING_STRATEGY: revision={revision_id}, weight=1")
        print(f"  [dry-run] ROUTING_RULE: auth_key_id={auth_key_id} → revision={revision_id}")
        return

    targets = client.get_targets(service_router_id)
    target_exists = any(
        str(target.get("resource_type")) == _CODE_SERVING_RESOURCE_TYPE
        and int(target.get("resource_id")) == code_serving_id
        and target.get("resource_revision_id") is not None
        and int(target["resource_revision_id"]) == revision_id
        for target in targets
    )
    if not target_exists:
        next_targets = [_target_payload(target) for target in targets]
        next_targets.append({
            "resource_type": _CODE_SERVING_RESOURCE_TYPE,
            "resource_id": code_serving_id,
            "resource_revision_id": revision_id,
            "model_type": _CODE_SERVING_MODEL_TYPE,
        })
        client.replace_targets(service_router_id, next_targets)
    else:
        print("  ✓ 서비스 라우터 target 이미 등록됨")

    policies = client.get_policies(service_router_id)
    client.upsert_routing_strategy_weights(
        router_id=service_router_id,
        new_revision_ids=[revision_id],
        existing_policies=policies,
        weight=1,
    )

    policies = client.get_policies(service_router_id)
    client.batch_update_routing_rules(
        router_id=service_router_id,
        updates={auth_key_id: revision_id},
        existing_policies=policies,
    )

    # 저장 API 성공만 믿지 않고 라우터 상태를 다시 읽어 세 조건을 모두 확인한다.
    targets = client.get_targets(service_router_id)
    policies = client.get_policies(service_router_id)
    target_ok = any(
        str(target.get("resource_type")) == _CODE_SERVING_RESOURCE_TYPE
        and int(target.get("resource_id")) == code_serving_id
        and target.get("resource_revision_id") is not None
        and int(target["resource_revision_id"]) == revision_id
        for target in targets
    )
    strategy_ok = any(
        policy.get("policy_type") == "ROUTING_STRATEGY"
        and policy.get("enabled", True)
        and any(
            item.get("revision_id") is not None
            and int(item["revision_id"]) == revision_id
            and int(item.get("weight") or 0) > 0
            for item in ((policy.get("config_json") or {}).get("representative_revisions") or [])
        )
        for policy in policies
    )
    rule_ok = any(
        policy.get("policy_type") == "ROUTING_RULE"
        and policy.get("enabled", True)
        and _policy_auth_key_id(policy) == auth_key_id
        and _policy_route_to(policy) == revision_id
        for policy in policies
    )
    if not (target_ok and strategy_ok and rule_ok):
        raise RuntimeError(
            "서비스 라우터 전환 검증 실패: "
            f"target={target_ok}, strategy={strategy_ok}, rule={rule_ok}"
        )
    print(f"  ✓ 규칙 기반 라우팅 전환 검증 완료: auth_key_id={auth_key_id} → {revision_id}")


def zero_direct_code_serving_weights(
    client: CheckedGenosClient,
    code_serving_id: int,
) -> None:
    """서비스 라우터 전용 사용을 위해 코드서빙 direct endpoint weight를 모두 0으로 만든다."""
    if client.dry_run:
        print("  [dry-run] 코드서빙 direct endpoint weight 전체 0")
        return

    revisions = client.list_revisions(code_serving_id, pg_size=_REVISION_LIST_PAGE_SIZE)
    weighted = {
        int(revision["id"]): int(revision.get("weight") or 0)
        for revision in revisions
        if revision.get("id") is not None and int(revision.get("weight") or 0) > 0
    }
    if not weighted:
        print("  ✓ 코드서빙 direct endpoint weight 변경 없음(이미 전체 0)")
        return

    payload = {
        "code_serving_id": code_serving_id,
        "representative_revisions": [
            {"code_serving_revision_id": int(revision["id"]), "weight": 0}
            for revision in revisions
            if revision.get("id") is not None
        ],
    }
    client._session.post(
        f"{client.base_url}/serving/code/{code_serving_id}/revisions/weights",
        headers=client._headers(),
        json=payload,
    )

    verified = client.list_revisions(code_serving_id, pg_size=_REVISION_LIST_PAGE_SIZE)
    stale = {
        int(revision["id"]): int(revision.get("weight") or 0)
        for revision in verified
        if revision.get("id") is not None and int(revision.get("weight") or 0) > 0
    }
    if stale:
        raise RuntimeError(f"코드서빙 direct weight 0 저장 검증 실패: {stale}")
    print(f"  ✓ 코드서빙 direct endpoint 트래픽 해제 완료: {weighted}")


def cleanup_unused_router_revisions(
    client: CheckedGenosClient,
    code_serving_id: int,
    service_router_id: int,
    keep_revision_id: int,
) -> None:
    """어떤 활성 ROUTING_RULE도 참조하지 않는 이 코드서빙의 이전 리비전을 정리한다."""
    print(f"\n{'='*60}")
    print("  [이전 리비전 정리] 미참조 target 제거 → 리비전 중지")
    print(f"{'='*60}")

    if client.dry_run:
        print(f"  [dry-run] revision_id={keep_revision_id} 및 다른 활성 규칙의 대상은 유지")
        return

    targets = client.get_targets(service_router_id)
    policies = client.get_policies(service_router_id)
    active_revision_ids = {
        route_to
        for policy in policies
        if policy.get("policy_type") == "ROUTING_RULE" and policy.get("enabled", True)
        if (route_to := _policy_route_to(policy)) is not None
    }
    active_revision_ids.add(keep_revision_id)

    stale_ids = sorted({
        int(target["resource_revision_id"])
        for target in targets
        if str(target.get("resource_type")) == _CODE_SERVING_RESOURCE_TYPE
        and int(target.get("resource_id")) == code_serving_id
        and target.get("resource_revision_id") is not None
        and int(target["resource_revision_id"]) not in active_revision_ids
    })
    if not stale_ids:
        print(f"  ✓ 정리할 이전 리비전 없음 (활성 route_to={sorted(active_revision_ids)})")
        return

    print(f"  활성 route_to(유지): {sorted(active_revision_ids)}")
    print(f"  이전 리비전(정리): {stale_ids}")
    client.remove_from_routing_strategy(service_router_id, set(stale_ids), policies)

    next_targets = [
        _target_payload(target)
        for target in targets
        if not (
            str(target.get("resource_type")) == _CODE_SERVING_RESOURCE_TYPE
            and int(target.get("resource_id")) == code_serving_id
            and target.get("resource_revision_id") is not None
            and int(target["resource_revision_id"]) in stale_ids
        )
    ]
    client.replace_targets(service_router_id, next_targets)

    revisions = client.list_revisions(code_serving_id, pg_size=_REVISION_LIST_PAGE_SIZE)
    status_by_id = {
        int(revision["id"]): revision.get("status")
        for revision in revisions
        if revision.get("id") is not None
    }
    errors: list[str] = []
    for revision_id in stale_ids:
        if status_by_id.get(revision_id) == _REVISION_STATUS_STOPPED:
            continue
        try:
            client.stop_revision(code_serving_id, revision_id)
        except Exception as exc:
            errors.append(f"revision_id={revision_id}: {exc}")
    if errors:
        raise RuntimeError("이전 리비전 중지 실패: " + "; ".join(errors))
    print(f"  ✓ 이전 리비전 {len(stale_ids)}개 정리 완료")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="코드서빙 배포 + 서비스 라우터 규칙 기반 라우팅 전환"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), metavar="PATH",
                        help=f"서비스 라우터 설정 파일 (기본: {DEFAULT_CONFIG.name})")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev",
                        help="전환할 인증키 환경 (기본: dev)")
    parser.add_argument("--branch", default=None,
                        help="배포할 브랜치. --commit 미지정 시 이 브랜치의 최신 커밋")
    parser.add_argument("--commit", default=None,
                        help="배포할 커밋(최근 30개 내 검증, short hash 지원)")
    parser.add_argument("--revision", type=int, default=None,
                        help="--route-only에서 라우팅할 기존 revision_id")
    phase_group = parser.add_mutually_exclusive_group()
    phase_group.add_argument("--deploy-only", action="store_true",
                             help="새 리비전 배포만 수행하고 라우터는 변경하지 않음")
    phase_group.add_argument("--route-only", action="store_true",
                             help="배포 없이 --revision 리비전으로 라우터 전환")
    parser.add_argument("--keep-previous", action="store_true",
                        help="라우팅 전환 후 이전 라우터 target/리비전 정리를 생략")
    parser.add_argument("--keep-failed", action="store_true",
                        help="배포 실패 리비전 자동 중지를 생략")
    parser.add_argument("--dry-run", action="store_true", help="실제 API 변경 없이 흐름 출력")
    parser.add_argument("--debug", action="store_true", help="API 응답 디버그 출력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.route_only and args.revision is None:
        print("[ERROR] --route-only에는 --revision ID가 필요합니다.", file=sys.stderr)
        sys.exit(2)
    if not args.route_only and args.revision is not None:
        print("[WARN] --revision은 --route-only에서만 사용합니다. 지정값을 무시합니다.", file=sys.stderr)

    try:
        cfg = load_service_config(args.config)
    except Exception as exc:
        print(f"[ERROR] 설정 로드 실패: {exc}", file=sys.stderr)
        sys.exit(2)

    genos_cfg = cfg["genos"]
    deployment_cfg = cfg["deployment"][args.env]
    code_serving_id = int(cfg["code_serving_id"])
    service_router_id = int(cfg["service_router_id"])
    auth_key_id = int(cfg["auth_key_id"][args.env])
    envs = [
        item for item in (cfg.get("envs") or [])
        if str(item.get("value", "")).strip()
    ]

    if not genos_cfg.get("password") and not args.dry_run:
        print("[ERROR] GENOS_PASSWORD 또는 config.yaml.serving의 GenOS 패스워드가 필요합니다.",
              file=sys.stderr)
        sys.exit(2)

    print("\n[코드서빙 + 서비스 라우터 배포]")
    print(f"  config: {Path(args.config).expanduser().resolve()}")
    print(f"  env/auth_key_id: {args.env}/{auth_key_id}")
    print(f"  code_serving_id: {code_serving_id}")
    print(f"  service_router_id: {service_router_id}")
    print(
        "  deployment: "
        f"docker_image_id={deployment_cfg['docker_image_id']}, "
        f"instance_type_id={deployment_cfg['instance_type_id']}, "
        f"replicas={deployment_cfg['replicas']}"
    )
    if args.branch:
        print(f"  branch: {args.branch}")
    if args.dry_run:
        print("  ※ dry-run 모드 — 실제 변경 없음")

    client = CheckedGenosClient(
        base_url=genos_cfg["base_url"],
        username=genos_cfg["username"],
        password=genos_cfg.get("password", ""),
        dry_run=args.dry_run,
        debug=args.debug,
    )
    if not args.dry_run:
        client.login()

    revision_id: int
    if args.route_only:
        revision_id = args.revision
        if not args.dry_run:
            print(f"  route-only 리비전 APP_RUNNING 확인 중: revision_id={revision_id}")
            try:
                client.wait_for_container_ready(
                    code_serving_id=code_serving_id,
                    revision_id=revision_id,
                    poll_interval=deployment_cfg["poll_interval_sec"],
                    timeout=deployment_cfg["poll_timeout_sec"],
                )
            except Exception as exc:
                print(f"[FAIL] route-only 리비전 준비 확인 실패: {exc}", file=sys.stderr)
                sys.exit(1)
    else:
        if args.commit:
            commit_hash = args.commit
            if not args.dry_run:
                print(f"  커밋 검증 중: {commit_hash}")
                client.validate_commit(code_serving_id, commit_hash, branch=args.branch)
        elif args.dry_run:
            commit_hash = "dry-run-hash"
        else:
            print(f"  최신 커밋 조회 중{f' (branch={args.branch})' if args.branch else ''}...")
            commit_hash = client.get_latest_commit(code_serving_id, branch=args.branch)
            print(f"  커밋: {commit_hash}")

        try:
            if not args.debug and not args.dry_run:
                client.quiet = True
                try:
                    with DeploymentProgress(code_serving_id) as progress:
                        revision_id = deploy_code_serving(
                            client=client,
                            deployment_cfg=deployment_cfg,
                            code_serving_id=code_serving_id,
                            commit_hash=commit_hash,
                            envs=envs,
                            cleanup_on_failure=not args.keep_failed,
                            progress=progress,
                        )
                finally:
                    client.quiet = False
            else:
                revision_id = deploy_code_serving(
                    client=client,
                    deployment_cfg=deployment_cfg,
                    code_serving_id=code_serving_id,
                    commit_hash=commit_hash,
                    envs=envs,
                    cleanup_on_failure=not args.keep_failed,
                )
        except Exception as exc:
            print(f"\n[FAIL] 새 리비전 배포 실패: {exc}", file=sys.stderr)
            sys.exit(1)

        if args.deploy_only:
            print(f"\n✓ 리비전 배포 완료: revision_id={revision_id}")
            if not args.dry_run:
                print("다음 명령으로 라우팅을 전환하세요:")
                script_path = shlex.quote(str(Path(__file__).resolve()))
                project_path = shlex.quote(str(Path(__file__).resolve().parent))
                config_path = shlex.quote(str(Path(args.config).expanduser().resolve()))
                print(
                    f"uv run --project {project_path} python {script_path} "
                    f"--config {config_path} "
                    f"--env {args.env} --route-only "
                    f"--revision {revision_id}"
                )
            return

    try:
        update_and_verify_service_router(
            client=client,
            code_serving_id=code_serving_id,
            service_router_id=service_router_id,
            auth_key_id=auth_key_id,
            revision_id=revision_id,
        )
        zero_direct_code_serving_weights(client, code_serving_id)
        if not args.keep_previous:
            cleanup_unused_router_revisions(
                client=client,
                code_serving_id=code_serving_id,
                service_router_id=service_router_id,
                keep_revision_id=revision_id,
            )
    except Exception as exc:
        print(
            f"\n[FAIL] revision_id={revision_id} 배포/준비 후 서비스 라우터 전환 실패: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  ✓ 서비스 라우터 전환 완료 — revision_id={revision_id}")
    print(f"  auth_key_id={auth_key_id} → route_to={revision_id}")
    if args.keep_previous:
        print("  (--keep-previous — 이전 target/리비전 정리 생략)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

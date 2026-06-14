# ruff: noqa: S101, S603, S607
# S101 — asserts narrow types from json.loads for the reader
# S603 — subprocess inputs are hard-coded constants, not user-supplied
# S607 — the `aws` / `git` executables are required to be on PATH for ops
"""Deploy enma-backend to AWS ECS — digest-pinned, rollout-verified.

Why this exists
---------------
Today (2026-06-14) we wasted hours on a Fargate cache: every code-only
deploy since R2 pushed a new image to ECR under the ``:latest`` tag,
ran ``aws ecs update-service --force-new-deployment``, and the polling
script reported "STABLE" without the running task ever being replaced.
The fix is to pin the task definition to the exact ``@sha256:DIGEST``
of the freshly built image, register that as a new revision, and only
declare success once the running task's ``taskDefinitionArn`` actually
matches the new revision.

Usage
-----
    python scripts/deploy.py [--build] [--no-wait]

``--build`` (default true) triggers a CodeBuild from the current HEAD
of the repo. ``--no-wait`` skips the rollout-verification loop, useful
when you just want to register the new task def and return.

Always prints the new image digest, new task def ARN, and the running
task's ARN + image digest at the end. Exits non-zero if the running
task's image digest doesn't match the one we registered within
``ROLLOUT_TIMEOUT_S`` seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REGION = "ap-south-1"
CLUSTER = "enma-prod"
SERVICE = "enma-backend"
ECR_REPO = "enma-backend"
S3_SOURCE_BUCKET = "enma-build-source-603013471251"
S3_SOURCE_KEY = "enma-backend.zip"
CODEBUILD_PROJECT = "enma-backend-build"
ROLLOUT_TIMEOUT_S = 600
POLL_INTERVAL_S = 15


def aws(args: list[str], *, capture: bool = True) -> str:
    """Run ``aws ...`` and return stdout (text). Raises on non-zero exit.

    When ``capture=False``, the subprocess inherits stdout/stderr from the
    terminal (so progress streams visibly) and we return an empty string —
    callers using ``capture=False`` are always after the side-effect, not
    the output.
    """
    cmd = ["aws", "--region", REGION, *args]
    proc = subprocess.run(cmd, check=True, capture_output=capture, text=True)
    return proc.stdout.strip() if proc.stdout is not None else ""


def aws_json(args: list[str]) -> object:
    return json.loads(aws(args))


def build_zip(repo_root: Path, dest: Path) -> None:
    """``git archive`` the enma-backend tree from HEAD into ``dest``."""
    subprocess.run(
        ["git", "archive", "--format=zip", "HEAD:enma-backend", "-o", str(dest)],
        check=True,
        cwd=repo_root,
    )


def upload_to_s3(local: Path) -> None:
    aws(
        [
            "s3",
            "cp",
            str(local),
            f"s3://{S3_SOURCE_BUCKET}/{S3_SOURCE_KEY}",
        ],
        capture=False,
    )


def start_codebuild() -> str:
    result = aws_json(
        ["codebuild", "start-build", "--project-name", CODEBUILD_PROJECT]
    )
    assert isinstance(result, dict)
    return str(result["build"]["id"])


def wait_for_codebuild(build_id: str) -> None:
    print(f"  build_id={build_id} waiting for SUCCEEDED...")
    while True:
        result = aws_json(
            ["codebuild", "batch-get-builds", "--ids", build_id]
        )
        assert isinstance(result, dict)
        status = result["builds"][0]["buildStatus"]
        if status == "IN_PROGRESS":
            time.sleep(POLL_INTERVAL_S)
            continue
        if status != "SUCCEEDED":
            raise SystemExit(f"CodeBuild failed: {status}")
        print(f"  build {build_id} ->SUCCEEDED")
        return


def latest_ecr_digest() -> str:
    """Return the digest of the image most-recently pushed to ECR."""
    result = aws_json(
        [
            "ecr",
            "describe-images",
            "--repository-name",
            ECR_REPO,
            "--query",
            "sort_by(imageDetails,&imagePushedAt)[-1].{digest:imageDigest,pushed:imagePushedAt}",
        ]
    )
    assert isinstance(result, dict)
    return str(result["digest"])


def current_task_def() -> dict:
    return aws_json(
        ["ecs", "describe-task-definition", "--task-definition", SERVICE]
    )["taskDefinition"]  # type: ignore[index, return-value]


def register_pinned_task_def(image_uri_with_digest: str) -> str:
    """Clone the current task def, set image to the digest-pinned URI,
    register a new revision, and return its ARN."""
    td = current_task_def()
    for key in (
        "taskDefinitionArn",
        "revision",
        "status",
        "requiresAttributes",
        "compatibilities",
        "registeredAt",
        "registeredBy",
        "deregisteredAt",
    ):
        td.pop(key, None)
    td["containerDefinitions"][0]["image"] = image_uri_with_digest

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(td, tmp, indent=2)
        tmp_path = tmp.name
    try:
        result = aws_json(
            [
                "ecs",
                "register-task-definition",
                "--cli-input-json",
                f"file://{tmp_path}",
            ]
        )
    finally:
        os.unlink(tmp_path)
    assert isinstance(result, dict)
    return str(result["taskDefinition"]["taskDefinitionArn"])


def point_service_at(task_def_arn: str) -> None:
    aws(
        [
            "ecs",
            "update-service",
            "--cluster",
            CLUSTER,
            "--service",
            SERVICE,
            "--task-definition",
            task_def_arn,
            "--force-new-deployment",
            "--query",
            "service.deployments[0].status",
            "--output",
            "text",
        ],
        capture=False,
    )


def wait_for_running_task_on(task_def_arn: str, image_digest: str) -> None:
    """Poll until a RUNNING task is on the expected task def AND image."""
    expected_rev = task_def_arn.rsplit(":", maxsplit=1)[-1]
    print(f"  waiting for RUNNING task on rev {expected_rev} (digest {image_digest[:19]}...)")
    deadline = time.time() + ROLLOUT_TIMEOUT_S
    while time.time() < deadline:
        tasks = aws_json(
            [
                "ecs",
                "list-tasks",
                "--cluster",
                CLUSTER,
                "--service-name",
                SERVICE,
                "--desired-status",
                "RUNNING",
            ]
        )
        assert isinstance(tasks, dict)
        task_arns = tasks.get("taskArns") or []
        if not task_arns:
            time.sleep(POLL_INTERVAL_S)
            continue
        described = aws_json(
            [
                "ecs",
                "describe-tasks",
                "--cluster",
                CLUSTER,
                "--tasks",
                *task_arns,
                "--query",
                "tasks[].{td:taskDefinitionArn,health:healthStatus,digest:containers[0].imageDigest}",
            ]
        )
        assert isinstance(described, list)
        for entry in described:
            assert isinstance(entry, dict)
            if (
                entry.get("td") == task_def_arn
                and entry.get("digest") == image_digest
                and entry.get("health") == "HEALTHY"
            ):
                print(f"  task on {expected_rev} is HEALTHY with expected digest")
                return
        time.sleep(POLL_INTERVAL_S)
    raise SystemExit(
        f"Rollout failed: no RUNNING task on {expected_rev} with "
        f"digest {image_digest[:19]}... within {ROLLOUT_TIMEOUT_S}s"
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy enma-backend to ECS.")
    parser.add_argument(
        "--build",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run CodeBuild before deploying (default: yes).",
    )
    parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for the running task to be on the new revision (default: yes).",
    )
    args = parser.parse_args()

    root = repo_root()

    if args.build:
        print("[1/5] Build zip from HEAD...")
        zip_path = root / "enma-backend-deploy.zip"
        if zip_path.exists():
            zip_path.unlink()
        build_zip(root, zip_path)
        print(f"  wrote {zip_path} ({zip_path.stat().st_size} bytes)")

        print("[2/5] Upload to S3...")
        upload_to_s3(zip_path)

        print("[3/5] Start CodeBuild...")
        build_id = start_codebuild()
        wait_for_codebuild(build_id)
    else:
        print("[1-3/5] --no-build, using whatever's currently at ECR :latest")

    print("[4/5] Pin task def to the freshly-built digest...")
    digest = latest_ecr_digest()
    account = aws(["sts", "get-caller-identity", "--query", "Account", "--output", "text"])
    image_uri = f"{account}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}@{digest}"
    print(f"  digest={digest[:19]}...")
    print(f"  image={image_uri}")
    new_td_arn = register_pinned_task_def(image_uri)
    print(f"  registered {new_td_arn}")

    print("[5/5] Point service at the new revision + verify rollout...")
    point_service_at(new_td_arn)
    if args.wait:
        wait_for_running_task_on(new_td_arn, digest)
        print("DEPLOY OK")
    else:
        print("DEPLOY KICKED OFF (skipped wait)")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"command failed: {' '.join(exc.cmd)}\n{exc.stderr}")

"""Code-driven orchestrator: `python -m lister drive SKU`.

Runs each role headless (`claude -p` or `codex exec`), in order, with pricing and description in
parallel. Every role is a planner -> generator -> evaluator loop:

- planner: code writes the brief (`crew.brief`), adding corrections from earlier rounds;
- generator: the role agent writes `output.json`;
- evaluator: code checks first (`crew.save`); then, when `lister/roles/<role>.eval.md` exists, a
  model from another vendor reviews the output and writes `eval.json` ({"pass", "fixes"}).

Failures become corrections for the next round, up to `max_rounds`. The run stops after
assembly: sign-off and publishing stay with the user.

Each role works in its own folder, `data/runs/SKU/<role>/`: brief.md, output.json, log.txt and,
when evaluated, eval.brief.md and eval.json.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from . import assemble, crew, preview
from .config import ROOT, Settings
from .models import CrewRun, Item
from .store import Store

log = logging.getLogger(__name__)

STAGES: tuple[tuple[str, ...], ...] = (("edition",), ("condition",), ("pricing", "description"))
CLAUDE_TOOLS = "Read Write Glob Grep WebSearch WebFetch"
# Keys loaded from .env would make the CLIs bill the API instead of the user's subscription.
API_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY")
GUARD = {  # Claude has file tools; Codex reads files through its (sandboxed) shell
    "claude": "Do not run shell commands or any `lister` command, and do not change anything on eBay or any "
              "other website: read only.",
    "codex": "Use read-only shell commands (cat, ls, rg) and image viewing to read files. Write only the output "
             "file the brief names. Do not run any `lister` command, and do not change anything on eBay or any "
             "other website.",
}


def step_dir(settings: Settings, sku: str, role: str) -> Path:
    path = settings.data_dir / "runs" / sku / role
    path.mkdir(parents=True, exist_ok=True)
    return path


def eval_prompt_path(role: str) -> Path:
    return crew.ROLES_DIR / f"{role}.eval.md"


def command(settings: Settings, role: str, prompt: str, engine: str) -> list[str]:
    """The headless command for one agent call."""
    conf = settings.orchestrator.roles.get(role, {})
    effort = conf.get("effort", "medium")
    if engine == "claude":
        tools = CLAUDE_TOOLS + (" mcp__claude-in-chrome" if conf.get("chrome") else "")
        cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools", tools,
               "--effort", effort]
        if conf.get("claude"):
            cmd += ["--model", conf["claude"]]
        if conf.get("chrome"):
            cmd.append("--chrome")
        return cmd
    if engine == "codex":
        cmd = ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write",
               "-c", f"model_reasoning_effort={effort}", "-c", "tools.web_search=true"]
        if conf.get("codex"):
            cmd += ["-m", conf["codex"]]
        return cmd + [prompt]
    raise ValueError(f"unknown engine {engine!r}; use claude or codex")


class AgentError(RuntimeError):
    """The agent CLI itself failed (auth, credit, crash): retrying won't help."""


@dataclass
class RoleReport:
    role: str
    rounds: int = 0
    ok: bool = False
    problems: list[str] = field(default_factory=list)


class Driver:
    def __init__(self, settings: Settings, store: Store, item: Item, engine: str | None = None,
                 dry_run: bool = False):
        self.settings, self.store, self.item = settings, store, item
        self.engine = engine or settings.orchestrator.engine
        self.dry_run = dry_run
        self.lock = threading.Lock()  # role threads share the item and the store

    # --- agent calls -------------------------------------------------------------

    def call(self, role: str, prompt: str, engine: str, log_path: Path) -> None:
        cmd = command(self.settings, role, prompt, engine)
        if self.dry_run:
            print(" ".join(cmd[:2]), "...", json.dumps(prompt))
            return
        log.info("%s: %s agent running", self.item.sku, role)
        env = {k: v for k, v in os.environ.items() if k not in API_KEYS}
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env,
                              timeout=self.settings.orchestrator.timeout_s)
        log_path.write_text(f"$ {' '.join(cmd[:2])}\n\n{proc.stdout}\n--- stderr ---\n{proc.stderr}", encoding="utf-8")
        if proc.returncode:
            raise AgentError(f"{role}: {engine} exited {proc.returncode}: "
                             f"{(proc.stdout or proc.stderr).strip()[:200]} (see {log_path})")

    # --- one role: plan -> generate -> evaluate, up to max_rounds ---------------------

    def run_role(self, role: str) -> RoleReport:
        folder = step_dir(self.settings, self.item.sku, role)
        brief_path, out_path = folder / "brief.md", folder / "output.json"
        report = RoleReport(role)
        for rnd in range(1, self.settings.orchestrator.max_rounds + 1):
            report.rounds = rnd
            with self.lock:
                brief_path.write_text(crew.brief(self.item, role, out_path, self.settings.seller.ebay_username),
                                      encoding="utf-8")
            out_path.unlink(missing_ok=True)
            self.call(role, f"Read `{brief_path}` and follow it exactly. {GUARD[self.engine]}", self.engine,
                      folder / "log.txt")
            if self.dry_run:
                return report
            failures = self.check(role, out_path)
            if not failures:
                failures = self.evaluate(role, brief_path, out_path)
            report.problems = failures
            if not failures:
                report.ok = True
                return report
            with self.lock:
                crew.correct(self.item, role, f"Round {rnd} failed these checks; fix every one:\n- "
                             + "\n- ".join(failures))
                self.store.save(self.item)
        return report

    def check(self, role: str, out_path: Path) -> list[str]:
        """Code checks: the file exists, matches the schema and passes the rules. Saves it."""
        if not out_path.exists():
            return [f"no output was written to {out_path}"]
        try:
            data = crew.load_json(out_path)
            with self.lock:
                problems = crew.save(self.item, role, data)
                self.store.save(self.item)
        except ValidationError as exc:
            return [f"schema: {e}" for e in crew.schema_errors(exc)]
        except ValueError as exc:
            return [str(exc)]
        return problems

    def evaluate(self, role: str, brief_path: Path, out_path: Path) -> list[str]:
        """Independent model review, when the role has an evaluator prompt."""
        prompt_file = eval_prompt_path(role)
        if not prompt_file.exists():
            return []
        folder = out_path.parent
        eval_brief, eval_out = folder / "eval.brief.md", folder / "eval.json"
        eval_brief.write_text("\n".join([
            prompt_file.read_text(encoding="utf-8").strip(), "",
            f"- The generator's brief (its instructions, facts and photos): `{brief_path}`",
            f"- The generator's output to evaluate: `{out_path}`", "",
            f'Write `{eval_out}` as JSON: {{"pass": true|false, "fixes": ["specific fix, with the evidence"]}}. '
            "Pass only when you would sign this off unchanged.",
        ]) + "\n", encoding="utf-8")
        eval_out.unlink(missing_ok=True)
        engine = self.settings.orchestrator.evaluator_engine
        self.call(role, f"Read `{eval_brief}` and follow it exactly. {GUARD[engine]}", engine,
                  folder / "eval.log.txt")
        try:
            verdict = json.loads(eval_out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AgentError(f"{role}: the evaluator wrote no verdict (see {folder / 'eval.log.txt'})")
        return [] if verdict.get("pass") else [f"evaluator: {f}" for f in verdict.get("fixes", [])] or \
            ["evaluator: failed without giving fixes"]

    # --- the whole run -----------------------------------------------------------

    def run(self, only: tuple[str, ...] = ()) -> dict:
        if self.item.crew is None:
            self.item.crew = CrewRun()
        reports: dict[str, RoleReport] = {}
        for stage in STAGES:
            roles = [r for r in stage if not only or r in crew.dependents(only)]
            if not roles:
                continue
            try:
                with ThreadPoolExecutor(len(roles)) as pool:
                    for rep in pool.map(self.run_role, roles):
                        reports[rep.role] = rep
            except AgentError as exc:
                return self.summary(reports, f"stopped: agent failed: {exc}")
            if self.dry_run:
                continue
            missing = [r for r in roles if not self.item.crew.roles[r].latest]
            if missing:  # later stages need these as given facts
                return self.summary(reports, f"stopped: no valid output from {', '.join(missing)}")
        if self.dry_run:
            return {}
        try:
            listing = assemble.assemble(self.item)
        except ValueError as exc:
            return self.summary(reports, f"not assembled: {exc}")
        self.store.save(self.item)
        out = preview.build(self.settings, self.store)
        return self.summary(reports, "assembled; review the preview, then `run signoff` when happy",
                            problems=listing.problems, questions=listing.questions, preview=str(out),
                            title=listing.title, price=listing.price, grade=listing.grade)

    def summary(self, reports: dict[str, RoleReport], status: str, **extra) -> dict:
        return {"sku": self.item.sku, "track": self.item.track, "status": status,
                "roles": {r: {"rounds": rep.rounds, "ok": rep.ok, "problems": rep.problems}
                          for r, rep in reports.items()}, **extra}

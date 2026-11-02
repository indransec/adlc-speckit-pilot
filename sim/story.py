"""Story runner: plays sim/story_e2e.yaml through the real lifecycle engine on the simulation calendar.

Every step acts through the same functions the API and UI use (intake, persona signatures, releases,
change requests, CI actions, deployments, live traffic), so the board, ledger and gate records end up
exactly as if people had done it. Expectations stop the run when the lifecycle does not behave as the
story says (for example a bad release that is NOT stopped at G3).

Interactive mode leaves gate and change-request decisions to a person in the UI: the runner waits.
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import timedelta
from typing import Callable

import yaml

from shared import changes, clock, ledger, lifecycle, releases, usecase
from shared.config import ROOT, evidence_root, project_root, release

APPROVED = ("approved", "approved_with_conditions")


class StoryError(RuntimeError):
    pass


def load(path: str | None = None) -> dict:
    return yaml.safe_load(open(path or ROOT / "sim" / "story_e2e.yaml"))


def golden_case(scenario: str) -> dict:
    """A new golden case in the dataset's format, numbered after the last one in the workspace."""
    from synthetic.scenarios import build_case
    p = project_root() / "evals" / "datasets" / "golden" / "claims_cases.jsonl"
    n = sum(1 for line in p.read_text().splitlines() if line.strip()) + 1
    cid = f"GLD-{n:03d}"
    return {"case_id": cid, **build_case(scenario, random.Random(n), f"CLM-{cid}")}


class Story:
    def __init__(self, story: dict, *, interactive: bool = False, log: Callable[[str], None] = print,
                 overrides: dict | None = None):
        self.story, self.interactive, self.out = story, interactive, log
        self.settings = {**story.get("settings", {}), **(overrides or {})}
        self.log_path = evidence_root() / "story-log.jsonl"

    # ------------------------------------------------------------------------------ plumbing
    def log(self, msg: str):
        self.out(msg)

    @staticmethod
    def key(step: dict) -> str:
        """A step's identity is its content, so resuming still works after steps are added to the story."""
        return ledger.sha256(ledger.canonical(step))[:16]

    def _record(self, i: int, step: dict, ok: bool, output: str, done: bool = False):
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"step": i, "key": self.key(step), "do": step["do"], "day": step.get("day"), "ok": ok,
                                "output": output, "done": done, "at": clock.now_iso()}, default=str) + "\n")

    def completed(self) -> set[str]:
        """Steps finished in an earlier invocation on this run (the story resumes after them)."""
        if not self.log_path.exists():
            return set()
        return {r["key"] for r in map(json.loads, self.log_path.read_text().splitlines()) if r.get("done") and r.get("key")}

    def _goto(self, step: dict):
        if "day" not in step:
            return
        target = clock.at_day(step["day"], step.get("hour", 9))
        if target > clock.now():
            clock.set_time(target)

    @staticmethod
    def _people(by) -> list[str]:
        return by if isinstance(by, list) else [by]

    def _wait(self, what: str, done: Callable[[], bool]):
        self.log(f"    ⏸  waiting for you in the UI: {what}")
        while not done():
            time.sleep(2)

    # ------------------------------------------------------------------------------ actions
    def a_intake(self, s):
        return f"tier {usecase.save(usecase.template(), by=s['by'])['tier']}"

    def a_submit(self, s):
        usecase.submit(usecase.current_id(), by=s["by"])
        return "submitted for G0"

    def a_confirm_classification(self, s):
        usecase.confirm_classification(usecase.current_id(), by=s["by"])
        return "classification confirmed"

    def a_g1_confirm(self, s):
        uc = usecase.current_id()
        usecase.save_g1(uc, {**usecase.g1_template(uc), "thresholds_acknowledged": True, "slos_acknowledged": True}, by=s["by"])
        return "limits, KPIs, thresholds and SLOs confirmed"

    def a_sign(self, s):
        gate, decision, notes = s["gate"], s.get("decision", "approved"), s.get("notes", "")
        if self.interactive:
            people = ", ".join(self._people(s["by"]))
            self._wait(f"{'reject' if decision == 'rejected' else 'sign'} {gate} as {people} (ADLC board / Use case intake)",
                       lambda: lifecycle.records()["gates"][lifecycle.GATE_IDS.index(gate)]["status"] in (*APPROVED, "rejected"))
        else:
            for p in self._people(s["by"]):
                rec = lifecycle.sign(gate, p, decision, notes)
                if rec["status"] in (*APPROVED, "rejected"):
                    break
        rec = lifecycle.records()["gates"][lifecycle.GATE_IDS.index(gate)]
        return f"{gate} {rec['status']}" + (f" · manifest {rec.get('manifest_sha256', '')[:12]}" if rec.get("manifest_sha256") else "")

    def a_ci(self, s):
        env = {"EVAL_LLM_MODE": "live"} if self.settings.get("live_evals") else {}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            res = [(a, lifecycle.run_action(a)) for a in s["actions"]]
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        return " | ".join(f"{a}: {r['status']} - {(r['output'].splitlines() or [''])[-1][:110]}" for a, r in res)

    def a_cut_release(self, s):
        files = {}
        golden = list(s.get("golden_add") or [])
        if s.get("golden_from_overrides"):  # G7 loop-back: what adjusters overrode most becomes new eval cases
            from shared import db
            golden += [r["scenario"] for r in db.query(
                "SELECT c.scenario, COUNT(*) n FROM approvals a JOIN claims c USING (claim_id) WHERE a.override = 1 "
                "AND c.scenario IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT ?", [int(s["golden_from_overrides"])])]
        if golden:
            p = "evals/datasets/golden/claims_cases.jsonl"
            text = (project_root() / p).read_text()
            for sc in golden:
                text += json.dumps(golden_case(sc)) + "\n"
                (project_root() / p).write_text(text)  # golden_case numbers after the cases already written
            files[p] = text
        log_p = project_root() / "CHANGELOG.md"
        prior = log_p.read_text() if log_p.exists() else "# Changelog\n"
        files["CHANGELOG.md"] = prior + f"\n## {s['version']} ({clock.now().date()})\n- {s['message']}\n"
        r = releases.cut(s["version"], by=s["by"], message=s["message"] + (f" (new golden cases: {', '.join(golden)})" if golden else ""),
                         edits=s.get("edits"), files=files)
        if s.get("fault"):
            ledger.append("fault_injected", item=usecase.current_id(), fault=s["fault"], release=s["version"],
                          simulation=True)
        return f"release {r['version']} @ {r['commit'][:10]}" + (f" (injects {s['fault']})" if s.get("fault") else "")

    def a_close_release(self, s):
        releases.close(s["version"], state=s["state"], reason=s["reason"], by=s["by"])
        return f"{s['version']} closed: {s['state']}"

    def a_raise_cr(self, s):
        c = changes.raise_cr(s["kind"], by=s["by"], summary=s.get("note", ""))
        return f"{c['id']} raised: {c['title']}"

    def a_decide_cr(self, s):
        cr = next(c for c in reversed(changes.crs(release())) if c["status"] == "raised" and c["kind"] != "go_live")
        if self.interactive:
            self._wait(f"decide {cr['id']} ({cr['title']}) as {', '.join(self._people(s['by']))}",
                       lambda: changes.get(cr["id"])["status"] != "raised")
        else:
            for p in self._people(s["by"]):
                changes.decide(cr["id"], p, s.get("decision", "approved"), s.get("notes", ""))
        return f"{cr['id']} {changes.get(cr['id'])['status']}"

    def a_deploy(self, s):
        d = changes.deploy(s["phase"], by=s["by"])
        return f"{d['release']} deployed in {d['phase']} mode under {d['cr']}"

    def a_traffic(self, s):
        from shared import faults
        from sim import traffic
        weights = traffic.mix(self.settings.get("shadow_extra"), s.get("scale"))
        if s.get("fault"):  # a traffic fault (fraud ring, injection campaign) is the claim mix itself
            ledger.append("fault_injected", item=None, fault=s["fault"], release=release(), by="simulator",
                          note=faults.catalog()[s["fault"]]["injected_by"], simulation=True)
        per_day = s.get("per_day") or self.settings.get("per_day_live" if s.get("adjusters") else "per_day") or self.settings["per_day"]
        r = traffic.run(s["day"], s["days"], int(per_day), weights,
                        seed=s["day"] + 1000, adjusters=s.get("adjusters", False), log=self.log)
        if s.get("fault"):
            ledger.append("fault_cleared", item=None, fault=s["fault"], release=release(), by="simulator", simulation=True)
        return f"{r['claims']} claims processed, {len(r['errors'])} errors" + \
            (f", {r['decisions']} human decisions" if s.get("adjusters") else "")

    def a_inject(self, s):
        from shared import faults
        faults.inject(s["fault"], by=s.get("by", "simulator"), note=s.get("note", ""))
        return f"{s['fault']} injected: {faults.catalog()[s['fault']]['injected_by']}"

    def _check_api(self):
        import httpx
        try:
            run = httpx.get("http://localhost:8000/health", timeout=5).json().get("run")
        except httpx.HTTPError:
            run = None
        mine = os.environ.get("SIM_RUN_ID")
        if run != mine:
            raise StoryError(f"incidents need the API serving this run ({mine}) on :8000 and the alerting stack: "
                             f"`make sim-api` and `make sim-stack-up` (API reports run {run})")

    def a_incident(self, s):
        """Wait for the alert to open an incident, check the page went out, acknowledge, apply the remedy."""
        import time as _t
        from shared import faults, incidents
        from sim import responder
        self._check_api()
        t0 = _t.time()
        fault = s.get("detects") or s.get("clear")
        injected = [e["ts"] for e in ledger.events() if e["type"] == "fault_injected" and e.get("fault") == fault]
        inc = responder.wait_incident(s["alert"], s.get("timeout", 240), opened_after=injected[-1] if injected else None)
        detected_s = round(_t.time() - t0)
        primary, *rest = responder.on_call(inc["team"])
        paged = [primary]
        if not responder.wait_page(primary, t0 - 60):
            raise StoryError(f"{inc['id']}: no page to {primary} in Mailpit")
        if s.get("escalate"):  # the primary misses the page: GoAlert pages the next person after a minute
            if not rest or not responder.wait_page(rest[0], t0 - 60, 180):
                raise StoryError(f"{inc['id']}: GoAlert did not escalate to {rest[:1]}")
            paged.append(rest[0])
        inc = responder.mirror_ack(inc) if self.interactive else responder.acknowledge(inc, s["ack_by"])
        if s.get("clear"):
            faults.clear(s["clear"], by=inc["ack_by"], note=s.get("note", ""))
        if s.get("note"):
            incidents.note(inc["id"], inc["ack_by"], s["note"])
        if s.get("detects"):
            ledger.append("fault_detected", item=None, fault=s["detects"], alert=s["alert"], incident=inc["id"],
                          team=inc["team"], paged=paged, acknowledged_by=inc["ack_by"], seconds_to_incident=detected_s,
                          release=release(), simulation=True)
        acts = f"; automatic: {'; '.join(inc['actions'])}" if inc["actions"] else ""
        return (f"{inc['id']} {inc['severity']} {s['alert']} → {inc['team']}: paged {' → '.join(paged)}, "
                f"acknowledged by {inc['ack_by']}{acts}" + (f"; {s['clear']} cleared" if s.get("clear") else ""))

    def a_close_incident(self, s):
        from shared import incidents
        from sim import responder
        handled = [e["incident"] for e in ledger.events() if e["type"] == "fault_detected" and e.get("alert") == s["alert"]]
        inc = incidents.get(handled[-1]) if handled else incidents.find(s["alert"])  # the one the story responded to
        if inc is None or inc["status"] == "closed":
            raise StoryError(f"no open incident for {s['alert']}")
        responder.wait_resolved(inc["id"], s.get("timeout", 240))
        incidents.close(inc["id"], s["by"], s["rca"])
        return f"{inc['id']} closed by {s['by']}"

    def a_hypercare_report(self, s):
        r = lifecycle.run_action("hypercare_report", from_day=s["from_day"], to_day=s["to_day"])
        return r["output"].splitlines()[-1]

    def a_extend_hypercare(self, s):
        """The Head of Claims cannot approve G6: hypercare is extended with conditions (a rejection on record)."""
        lifecycle.sign("G6", s["by"], "rejected", f"hypercare extended to day {s['until_day']}: {s['conditions']}")
        ledger.append("hypercare_extended", item=release(), by=s["by"], until_day=s["until_day"], conditions=s["conditions"])
        return f"G6 not approved for {release()}; hypercare extended to day {s['until_day']}"

    def a_replay_shadow(self, s):
        r = changes.replay_shadow(int(s.get("n", 330)), by=s["by"])
        return f"{r['output']} under {r['cr']}"

    def a_report(self, s):
        from sim import report
        r = report.write()
        for line in report.matrix_lines(r):
            self.log("    " + line)
        return f"{r['passed']}/{r['total']} faults caught as expected · report {r['path']}"

    def a_runtime(self, s):
        from shared import teams
        from shared.runtime import set_runtime
        teams.persona(s["by"])
        for k, v in s["set"].items():
            set_runtime(k, v, by=s["by"])
        ledger.append("runtime_changed", item=None, by=s["by"], changes=s["set"], note=s.get("note", ""), release=release())
        return ", ".join(f"{k} = {v}" for k, v in s["set"].items())

    def a_shadow_report(self, s):
        r = lifecycle.run_action("shadow_report", n=int(self.settings["min_shadow_claims"]))
        return r["output"].splitlines()[-1]

    ACTIONS = {"intake", "submit", "confirm_classification", "g1_confirm", "sign", "ci", "cut_release", "close_release",
               "raise_cr", "decide_cr", "deploy", "traffic", "shadow_report", "inject", "incident", "close_incident", "runtime",
               "hypercare_report", "extend_hypercare", "replay_shadow", "report"}

    # ------------------------------------------------------------------------------ expectations
    def _expect(self, exp: dict) -> list[str]:
        problems = []
        for key, want in exp.items():
            if key == "shadow":
                rep = json.loads((lifecycle.ev_dir() / "shadow-report.json").read_text())
                got = "passed" if rep["passed"] else "failed"
            elif want in ("ready", "not_ready"):
                got = "ready" if lifecycle.check_gate(key)["ready"] else "not_ready"
            else:
                got = lifecycle.records()["gates"][lifecycle.GATE_IDS.index(key)]["status"]
            if got != want:
                problems.append(f"expected {key} {want}, got {got}")
        return problems

    def _detected(self, fault: str, step: dict):
        gate = {"shadow_report": "G5"}.get(step["do"], "G3")
        st = lifecycle.check_gate(gate)
        failing = [{"check": c["check"], "detail": c["detail"]} for c in st["checks"] if not c["ok"]
                   and (step["do"] != "shadow_report" or c["check"].startswith("Shadow"))]  # drills come later
        ledger.append("fault_detected", item=usecase.current_id(), fault=fault, gate=gate, release=release(),
                      failing_checks=failing, simulation=True)
        self.log(f"    🔎 {fault} caught at {gate}: " + "; ".join(f["check"] for f in failing))

    # ------------------------------------------------------------------------------ run
    def run(self, until_day: float | None = None) -> list[dict]:
        clock.pause()
        done, skip = [], self.completed()
        if skip:
            self.log(f"resuming: {len(skip)} steps already done in this run")
        for i, step in enumerate(self.story["steps"], start=1):
            if self.key(step) in skip:
                continue
            if until_day is not None and step.get("day", -1e9) > until_day:
                self.log(f"stopping before day {step['day']} (--until-day {until_day})")
                break
            if step["do"] not in self.ACTIONS:
                raise StoryError(f"step {i}: unknown action {step['do']}")
            self._goto(step)
            label = f"[day {clock.sim_day():+.1f}] {step['do']}" + (f" {step.get('gate') or step.get('version') or ''}")
            self.log(label.rstrip() + (f" — {step['note']}" if step.get("note") else ""))
            try:
                output = getattr(self, f"a_{step['do']}")(step)
                ok = True
            except Exception as e:
                output, ok = f"{type(e).__name__}: {e}", False
            self.log(f"    {'✓' if ok else '✗'} {output}")
            self._record(i, step, ok, output)
            if not ok and not step.get("expect"):
                raise StoryError(f"step {i} ({step['do']}) failed: {output}")
            if step.get("detects") and step["do"] != "incident":  # incidents record their own detection
                self._detected(step["detects"], step)
            problems = self._expect(step.get("expect", {}))
            if problems:
                raise StoryError(f"step {i} ({step['do']}): " + "; ".join(problems))
            self._record(i, step, ok, "expectations met", done=True)
            done.append({"step": i, **step, "output": output})
            clock.set_time(clock.now() + timedelta(minutes=5))
        return done

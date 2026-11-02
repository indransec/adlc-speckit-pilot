"""Alerting stack for the end-to-end simulation (deploy/sim/docker-compose.yml).

  python scripts/sim_stack.py up        generate config, start the containers, seed GoAlert, route alerts to it
  python scripts/sim_stack.py status    health of each component and the GoAlert services
  python scripts/sim_stack.py down      stop and remove the containers (GoAlert state is lost; `up` re-seeds)

Generated from the repo's single sources:
  observability/alerts.yaml -> Prometheus rules; ops/teams.yaml -> GoAlert users, escalation policies,
  services and integration keys -> Alertmanager routes (one receiver per team).
Each on-call persona gets a verified email contact method: GoAlert sends the verification code to
Mailpit and this script reads it back, as a person would.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import httpx
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sim.goalert import GoAlert, password, verification_code  # noqa: E402
SIM = os.path.join(ROOT, "deploy", "sim")
GEN = os.path.join(SIM, "generated")
STATE = os.path.join(GEN, "goalert-state.json")
GOALERT, MAILPIT, PROM, AM = "http://localhost:8081", "http://localhost:8025", "http://localhost:9090", "http://localhost:9093"
API = os.environ.get("SIM_API_URL", "http://host.docker.internal:8000")  # the API as seen from the containers
ESCALATE_AFTER_MIN = 1  # GoAlert's minimum step delay: the next person is paged after a minute without an ack


def _load(rel):
    return yaml.safe_load(open(os.path.join(ROOT, rel)))


def _write(name, text):
    os.makedirs(GEN, exist_ok=True)
    with open(os.path.join(GEN, name), "w") as f:  # rewritten in place: the containers mount the directory
        f.write(text)


# ------------------------------------------------------------------------------------- config generation
def prometheus_config() -> None:
    _write("prometheus.yml", yaml.safe_dump({
        "global": {"scrape_interval": "5s", "evaluation_interval": "5s"},
        "rule_files": ["/etc/prometheus/generated/rules.yml"],
        "alerting": {"alertmanagers": [{"static_configs": [{"targets": ["alertmanager:9093"]}]}]},
        "scrape_configs": [{"job_name": "adlc-api", "metrics_path": "/metrics",
                            "static_configs": [{"targets": [API.split("//")[1]]}]}]}, sort_keys=False))
    rules = []
    for a in _load("observability/alerts.yaml")["alerts"]:
        labels = {"severity": a["severity"], "team": a["team"]}
        if a.get("auto_action"):
            labels["auto_action"] = a["auto_action"]
        rules.append({"alert": a["name"], "expr": a["expr"], "for": "10s", "labels": labels,
                      "annotations": {"summary": a["condition"], "inform": ",".join(a.get("also_inform", [])),
                                      "value": "{{ $value }}"}})
    _write("rules.yml", yaml.safe_dump({"groups": [{"name": "adlc", "interval": "5s", "rules": rules}]}, sort_keys=False))


def alertmanager_config(keys: dict) -> None:
    """Every alert goes to the API (incident card, automatic action); the team's alerts also page in GoAlert."""
    receivers = [{"name": "adlc-api", "webhook_configs": [{"url": f"{API}/webhooks/alertmanager", "send_resolved": True}]}]
    routes = [{"receiver": "adlc-api", "continue": True}]
    for team, info in keys.items():
        receivers.append({"name": f"goalert-{team}",
                          "webhook_configs": [{"url": info["integration_url"], "send_resolved": True}]})
        routes.append({"receiver": f"goalert-{team}", "matchers": [f'team="{team}"']})
    _write("alertmanager.yml", yaml.safe_dump({
        "global": {"resolve_timeout": "1m"},
        "route": {"receiver": "adlc-api", "group_by": ["alertname", "release"], "group_wait": "5s",
                  "group_interval": "10s", "repeat_interval": "4h", "routes": routes},
        "receivers": receivers}, sort_keys=False))


def grafana_dashboard() -> None:
    panels, y = [], 0
    stat = ["adlc_sim_day", "adlc_runtime_kill_switch", "adlc_runtime_straight_through_enabled", "adlc_payments_without_valid_token",
            "adlc_stp_over_authority"]
    for i, m in enumerate(stat):
        panels.append({"type": "stat", "title": m.replace("adlc_", "").replace("_", " "), "gridPos": {"x": i * 4, "y": y, "w": 4, "h": 4},
                       "targets": [{"expr": m, "refId": "A"}], "datasource": {"uid": "prometheus"}})
    y += 4
    series = [("Override rate", ["adlc_override_rate"]), ("Tool error rate", ["adlc_tool_error_rate"]),
              ("Decision latency p95 (s)", ["adlc_decision_latency_p95_seconds"]),
              ("Cost per claim vs 7-day baseline (USD)", ["adlc_cost_per_claim_usd", "adlc_cost_per_claim_baseline_usd"]),
              ("Fraud-flag rate vs 7-day baseline", ["adlc_fraud_flag_rate", "adlc_fraud_flag_rate_baseline"]),
              ("Injection blocks per claim vs 7-day baseline", ["adlc_injection_block_rate", "adlc_injection_block_rate_baseline"])]
    for i, (title, exprs) in enumerate(series):
        panels.append({"type": "timeseries", "title": title, "gridPos": {"x": (i % 2) * 12, "y": y + (i // 2) * 7, "w": 12, "h": 7},
                       "datasource": {"uid": "prometheus"},
                       "targets": [{"expr": e, "refId": chr(65 + j), "legendFormat": e.replace("adlc_", "")} for j, e in enumerate(exprs)]})
    panels.append({"type": "alertlist", "title": "Firing alerts", "gridPos": {"x": 0, "y": y + 21, "w": 24, "h": 6},
                   "options": {"alertInstanceLabelFilter": "", "stateFilter": {"firing": True, "pending": True}}})
    dash = {"uid": "adlc-sim", "title": "ADLC simulation - hypercare", "refresh": "5s", "time": {"from": "now-1h", "to": "now"},
            "schemaVersion": 39, "panels": panels}
    with open(os.path.join(SIM, "grafana", "dashboards", "adlc.json"), "w") as f:
        json.dump(dash, f, indent=1)


# ------------------------------------------------------------------------------------- GoAlert
def seed() -> dict:
    teams = _load("ops/teams.yaml")
    people = {p["id"]: p for p in teams["personas"]}
    g = GoAlert()
    g.gql("mutation($in:[ConfigValueInput!]){ setConfig(input:$in) }", {"in": [
        {"id": "SMTP.Enable", "value": "true"}, {"id": "SMTP.From", "value": "goalert@claims.sim"},
        {"id": "SMTP.Address", "value": "mailpit:1025"}, {"id": "SMTP.DisableTLS", "value": "true"},
        {"id": "General.PublicURL", "value": GOALERT}]})
    users = {}
    for pid in sorted({p for t in teams["teams"] for p in t.get("on_call", [])}):
        p, email = people[pid], f"{pid}@claims.sim"
        uid = g.gql("mutation($in:CreateUserInput!){ createUser(input:$in){ id } }", {"in": {
            "username": pid, "password": password(pid), "name": p["name"], "email": email, "role": "user"}})["createUser"]["id"]
        cm = g.gql("mutation($in:CreateUserContactMethodInput!){ createUserContactMethod(input:$in){ id } }", {"in": {
            "userID": uid, "name": "Work email", "newUserNotificationRule": {"delayMinutes": 0},
            "dest": {"type": "builtin-smtp-email", "values": [{"fieldID": "email_address", "value": email}]}}})["createUserContactMethod"]["id"]
        g.gql("mutation($in:SendContactMethodVerificationInput!){ sendContactMethodVerification(input:$in) }", {"in": {"contactMethodID": cm}})
        g.gql("mutation($in:VerifyContactMethodInput!){ verifyContactMethod(input:$in) }",
              {"in": {"contactMethodID": cm, "code": verification_code(email)}})
        users[pid] = uid
        print(f"  user {pid:8} {p['name']:16} email verified")
    keys = {}
    for t in teams["teams"]:
        if not t.get("on_call"):
            continue
        steps = [{"delayMinutes": ESCALATE_AFTER_MIN, "targets": [{"id": users[pid], "type": "user"}]} for pid in t["on_call"]]
        ep = g.gql("mutation($in:CreateEscalationPolicyInput!){ createEscalationPolicy(input:$in){ id } }",
                   {"in": {"name": t["name"], "repeat": 0, "steps": steps}})["createEscalationPolicy"]["id"]
        svc = g.gql("mutation($in:CreateServiceInput!){ createService(input:$in){ id integrationKeys { href } } }", {"in": {
            "name": t["name"], "description": f"ADLC simulation: alerts routed to {t['id']}", "escalationPolicyID": ep,
            "newIntegrationKeys": [{"type": "prometheusAlertmanager", "name": "Alertmanager"}]}})["createService"]
        url = svc["integrationKeys"][0]["href"].replace(GOALERT, "http://goalert:8081")  # as seen from Alertmanager
        keys[t["id"]] = {"service_id": svc["id"], "escalation_policy_id": ep, "integration_url": url, "on_call": t["on_call"]}
        print(f"  service {t['name']:30} escalation: {' -> '.join(t['on_call'])}")
    state = {"users": users, "teams": keys}
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)
    return state


# ------------------------------------------------------------------------------------- commands
def _compose(*args):
    return subprocess.run(["docker", "compose", "-f", os.path.join(SIM, "docker-compose.yml"), *args], check=True)


def _wait(url: str, name: str, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError(f"{name} did not come up at {url}")


def _reload_alertmanager(attempts: int = 15) -> None:
    """The bind mount can lag behind the host file on macOS: reload until the team receivers are live."""
    for _ in range(attempts):
        httpx.post(f"{AM}/-/reload", timeout=10)
        if "goalert-" in httpx.get(f"{AM}/api/v2/status", timeout=10).json()["config"]["original"]:
            return
        time.sleep(1)
    raise RuntimeError("Alertmanager did not load the GoAlert receivers; see `docker logs adlc-sim-alertmanager-1`")


def up():
    prometheus_config()
    alertmanager_config({})
    grafana_dashboard()
    _compose("up", "-d")
    for url, name in [(f"{GOALERT}/health", "GoAlert"), (f"{MAILPIT}/api/v1/info", "Mailpit"),
                      (f"{PROM}/-/ready", "Prometheus"), (f"{AM}/-/ready", "Alertmanager")]:
        _wait(url, name)
    state = json.load(open(STATE)) if os.path.exists(STATE) else None
    try:
        GoAlert().gql("query($id:ID!){ service(id:$id){ id } }", {"id": state["teams"]["ai-oncall"]["service_id"]})["service"]["id"]
        print("GoAlert already seeded")
    except Exception:
        print("seeding GoAlert from ops/teams.yaml")
        state = seed()
    alertmanager_config(state["teams"])
    _reload_alertmanager()
    httpx.post(f"{PROM}/-/reload", timeout=10)
    print("stack up: Prometheus :9090 · Alertmanager :9093 · GoAlert :8081 (admin/admin123) · Mailpit :8025 · Grafana :3000")
    print(f"the API must listen on :8000 for scraping and webhooks (make sim-api); seen from the containers as {API}")


def status():
    for url, name in [(f"{PROM}/-/ready", "Prometheus"), (f"{AM}/-/ready", "Alertmanager"), (f"{GOALERT}/health", "GoAlert"),
                      (f"{MAILPIT}/api/v1/info", "Mailpit"), ("http://localhost:3000/api/health", "Grafana"),
                      ("http://localhost:8000/health", "API")]:
        try:
            ok = httpx.get(url, timeout=3).status_code < 400
        except httpx.HTTPError:
            ok = False
        print(f"  {'✓' if ok else '✗'} {name:13} {url}")
    try:
        t = httpx.get(f"{PROM}/api/v1/targets", timeout=3).json()["data"]["activeTargets"]
        print("  scrape:", ", ".join(f"{x['labels']['job']}={x['health']}" for x in t))
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "down", "status", "config"])
    a = ap.parse_args()
    if a.cmd == "up":
        up()
    elif a.cmd == "down":
        _compose("down", "-v")
        if os.path.exists(STATE):
            os.remove(STATE)
    elif a.cmd == "config":
        prometheus_config()
        alertmanager_config(json.load(open(STATE))["teams"] if os.path.exists(STATE) else {})
        grafana_dashboard()
        print(f"wrote {GEN}")
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main())

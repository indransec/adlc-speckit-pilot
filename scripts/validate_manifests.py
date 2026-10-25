"""Validate system.yaml and agents/*/agent.yaml against schemas and cross-references."""
import glob, json, os, sys
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = ["low", "medium", "high"]


def load_yaml(p):
    with open(p) as f:
        return yaml.safe_load(f)


def check_required(doc, schema, name, errors):
    for k in json.load(open(schema))["required"]:
        if k not in doc:
            errors.append(f"{name}: missing '{k}'")


def all_req_ids():
    ids = set()
    for p in glob.glob(os.path.join(ROOT, "docs", "requirements", "*.yaml")):
        ids |= {r["id"] for r in load_yaml(p)["requirements"]}
    return ids


def main():
    errors = []
    sysm = load_yaml(os.path.join(ROOT, "system.yaml"))
    check_required(sysm, os.path.join(ROOT, "schemas", "system.schema.json"), "system.yaml", errors)
    reqs = all_req_ids()
    tool_ids = {load_yaml(p)["id"] for p in glob.glob(os.path.join(ROOT, "tools", "*", "tool.yaml"))}
    agents = {}
    for p in glob.glob(os.path.join(ROOT, "agents", "*", "agent.yaml")):
        a = load_yaml(p)
        name = os.path.relpath(p, ROOT)
        check_required(a, os.path.join(ROOT, "schemas", "agent.schema.json"), name, errors)
        agents[a["id"]] = a
        for r in a.get("requirements", []):
            if r not in reqs:
                errors.append(f"{name}: unknown requirement {r}")
        for t in a.get("tools") or []:
            if t["id"] not in tool_ids:
                errors.append(f"{name}: unknown tool {t['id']}")
    pins = [x["ref"] for x in sysm["topology"]["agents"]]
    for ref in pins:
        aid, ver = ref.split("@")
        if aid not in agents:
            errors.append(f"system.yaml: pinned agent {aid} not found")
        elif agents[aid]["version"] != ver:
            errors.append(f"system.yaml: {aid} pinned {ver} but agent.yaml is {agents[aid]['version']}")
    computed = max((agents[a]["risk_tier"] for a in agents), key=TIERS.index)
    if TIERS.index(sysm["risk_tier"]) < TIERS.index(computed):
        errors.append(f"system.yaml: risk_tier {sysm['risk_tier']} lower than highest agent tier {computed}")
    for e in errors:
        print("ERROR", e)
    print(f"{len(agents)} agents, {len(tool_ids)} tools, {len(reqs)} requirements checked; {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

import re
import json
import random
import sys

# ─── Platform (fixed, from example) ───────────────────────────────────────────
PLATFORM = {
    "nodes": [
        {"id": 1,  "is_router": False},
        {"id": 2,  "is_router": False},
        {"id": 3,  "is_router": False},
        {"id": 4,  "is_router": True},
        {"id": 5,  "is_router": True},
        {"id": 6,  "is_router": True},
        {"id": 7,  "is_router": False},
        {"id": 8,  "is_router": True},
        {"id": 9,  "is_router": True},
        {"id": 10, "is_router": True},
        {"id": 11, "is_router": False},
        {"id": 12, "is_router": True},
        {"id": 13, "is_router": True},
        {"id": 14, "is_router": True},
        {"id": 15, "is_router": False},
        {"id": 16, "is_router": False},
        {"id": 17, "is_router": False},
    ],
    "links": [
        {"start": 4,  "end": 1},
        {"start": 5,  "end": 2},
        {"start": 6,  "end": 3},
        {"start": 4,  "end": 5},
        {"start": 4,  "end": 8},
        {"start": 5,  "end": 6},
        {"start": 5,  "end": 9},
        {"start": 6,  "end": 10},
        {"start": 8,  "end": 7},
        {"start": 8,  "end": 9},
        {"start": 9,  "end": 10},
        {"start": 8,  "end": 12},
        {"start": 9,  "end": 13},
        {"start": 10, "end": 14},
        {"start": 10, "end": 11},
        {"start": 12, "end": 13},
        {"start": 13, "end": 14},
        {"start": 12, "end": 15},
        {"start": 13, "end": 16},
        {"start": 14, "end": 17},
    ]
}

FREQUENCIES = [500, 1000]
SCHEMES = [{"id": 0, "wcdt": 0, "wcct": 0, "wccr": 1}]

# Non-router node IDs (computed from platform)
NON_ROUTER_NODES = [n["id"] for n in PLATFORM["nodes"] if not n["is_router"]]


# ─── TGFF Parser ──────────────────────────────────────────────────────────────
def parse_tgff(filepath):
    """Parse a .tgff file and return list of task graphs."""
    with open(filepath) as f:
        content = f.read()

    graphs = []
    # Match each @TASK_GRAPH block
    tg_blocks = re.findall(
        r'@TASK_GRAPH\s+(\d+)\s*\{([^}]*)\}', content, re.DOTALL
    )

    for tg_id, block in tg_blocks:
        tasks = {}   # local_name -> local index within this graph
        arcs  = []

        # Parse tasks
        for m in re.finditer(r'TASK\s+(t\d+_\d+)\s+TYPE\s+(\d+)', block):
            task_name, task_type = m.group(1), int(m.group(2))
            local_idx = len(tasks)
            tasks[task_name] = {"local_idx": local_idx, "type": task_type}

        # Parse arcs
        for m in re.finditer(
            r'ARC\s+(a\d+_\d+)\s+FROM\s+(t\d+_\d+)\s+TO\s+(t\d+_\d+)\s+TYPE\s+(\d+)',
            block
        ):
            arcs.append({
                "name":      m.group(1),
                "from_task": m.group(2),
                "to_task":   m.group(3),
                "type":      int(m.group(4)),
            })

        graphs.append({
            "id":    int(tg_id),
            "tasks": tasks,   # name -> {local_idx, type}
            "arcs":  arcs,
        })

    return graphs


# ─── JSON Generator ───────────────────────────────────────────────────────────
def generate_json_for_graph(graph):
    """Generate JSON for a SINGLE task graph."""

    # ── Assign local IDs ───────────────────────────────────────────────
    local_task_id = {}
    for i, task_name in enumerate(graph["tasks"].keys()):
        local_task_id[task_name] = i

    # ── Jobs ──────────────────────────────────────────────────────────
    jobs = []
    for task_name in graph["tasks"]:
        gid = local_task_id[task_name]

        k = random.randint(1, 3)
        can_run_on = sorted(random.sample(NON_ROUTER_NODES, k))

        jobs.append({
            "id": gid,
            "wcet_fullspeed": random.randint(10, 100),
            "mcet": 0,
            "processing_times": random.randint(1, 9),
            "can_run_on": can_run_on
        })

    # ── Messages ───────────────────────────────────────────────────────
    messages = []
    msg_id = 0

    for arc in graph["arcs"]:
        sender = arc["from_task"]
        receiver = arc["to_task"]

        if sender not in local_task_id or receiver not in local_task_id:
            continue

        messages.append({
            "id": msg_id,
            "sender": local_task_id[sender],
            "receiver": local_task_id[receiver],
            "size": random.randint(10, 30),
            "timetriggered": True,
            "period": random.randint(1, 10) * 10
        })
        msg_id += 1

    return {
        "application": {
            "jobs": jobs,
            "messages": messages
        },
        "platform": PLATFORM,
        "frequencies": FREQUENCIES,
        "schemes": SCHEMES
    }

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tgff_to_json.py <input.tgff>")
        sys.exit(1)

    tgff_path = sys.argv[1]

    random.seed(42)

    graphs = parse_tgff(tgff_path)

    print(f"Parsed {len(graphs)} task graphs.")

    for g in graphs:
        print(f"Processing Graph {g['id']}...")

        result = generate_json_for_graph(g)

        output_file = f"../input/graph_{g['id']}.json"

        with open(output_file, "w") as f:
            json.dump(result, f, indent=4)

        print(f"Written: {output_file}")
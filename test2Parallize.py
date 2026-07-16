import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from z3 import *
from util.KPathFinding2 import compute_k_paths

SUPPORTED_SOLVERS = ("z3", "cvc5", "boolector")
SOLVER_TIMEOUT_MS = 300000
DEFAULT_INPUT_FILE = "input/graph_0.json"

# ── module-level setup (safe to run in workers too) ──
input_file = None
data = None
jobs_data = []
messages_data = []
platform_nodes = []
app_deadline = None
endsystems = []
switches = []
all_nodes = []
num_endsystems = 0
num_switches = 0
num_nodes = 0
node_to_idx = {}
idx_to_node = {}
es_real_to_esidx = {}
adj = []
undirected_links = set()
path_data = {}
num_jobs = 0
num_msgs = 0


def load_input(new_input_file):
    global input_file, data, jobs_data, messages_data, platform_nodes, app_deadline
    global endsystems, switches, all_nodes
    global num_endsystems, num_switches, num_nodes
    global node_to_idx, idx_to_node, es_real_to_esidx
    global adj, undirected_links, path_data, num_jobs, num_msgs

    input_file = new_input_file
    with open(input_file, "r") as f:
        data = json.load(f)

    jobs_data      = data["application"]["jobs"]
    messages_data  = data["application"]["messages"]
    platform_nodes = data["platform"]["nodes"]
    app_deadline   = data["application"]["deadline"]

    node_ids = [node["id"] for node in platform_nodes]
    duplicate_ids = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
    if duplicate_ids:
        raise ValueError(
            "Platform contains duplicate node id(s): "
            + ", ".join(str(node_id) for node_id in duplicate_ids)
        )

    endsystems = sorted([n["id"] for n in platform_nodes if not n["is_router"]])
    switches   = sorted([n["id"] for n in platform_nodes if     n["is_router"]])
    all_nodes  = endsystems + switches

    num_endsystems = len(endsystems)
    num_switches   = len(switches)
    num_nodes      = len(all_nodes)

    node_to_idx      = {real_id: idx for idx, real_id in enumerate(all_nodes)}
    idx_to_node      = {idx: real_id for real_id, idx  in node_to_idx.items()}
    es_real_to_esidx = {real_id: i   for i, real_id    in enumerate(endsystems)}

    adj = [[False] * num_nodes for _ in range(num_nodes)]
    for link in data["platform"].get("links", []):
        i = node_to_idx[link["start"]]
        j = node_to_idx[link["end"]]
        adj[i][j] = True
        adj[j][i] = True

    undirected_links = set()
    for ni in range(num_nodes):
        for nj in range(num_nodes):
            if adj[ni][nj]:
                undirected_links.add((min(ni, nj), max(ni, nj)))

    path_data = compute_k_paths(input_file, k=1)
    num_jobs  = len(jobs_data)
    num_msgs  = len(messages_data)


load_input(DEFAULT_INPUT_FILE)


class SolverBackendError(RuntimeError):
    pass


class Z3ModelAdapter:
    def __init__(self, model):
        self.model = model

    def value(self, name):
        val = self.model[Int(name)]
        if val is None:
            raise SolverBackendError(f"Model does not contain value for {name}")
        return val.as_long()

    def maybe_value(self, name):
        var = Int(name)
        val = self.model.eval(var, model_completion=False)
        if val is None or str(val) == name:
            return None
        return val.as_long()


class DictModelAdapter:
    def __init__(self, values):
        self.values = values

    def value(self, name):
        if name not in self.values:
            raise SolverBackendError(f"Model does not contain value for {name}")
        return self.values[name]

    def maybe_value(self, name):
        return self.values.get(name)


def parse_solver_model(output):
    values = {}

    # CVC5 prints values as: (define-fun x () Int 12)
    for name, value in re.findall(
        r"\(define-fun\s+([^\s()]+)\s+\(\)\s+Int\s+(-?\d+)\)",
        output,
    ):
        values[name] = int(value)

    # Z3-style get-value fallback, useful if a solver is configured that way:
    # ((x 12))
    for name, value in re.findall(r"\(\(([^\s()]+)\s+(-?\d+)\)\)", output):
        values[name] = int(value)

    # SMT-LIB bit-vector model values, used by Boolector/CVC5 on QF_BV:
    # (define-fun x () (_ BitVec 10) #b0000001100)
    for name, _, value in re.findall(
        r"\(define-fun\s+([^\s()]+)\s+\(\)\s+\(_\s+BitVec\s+(\d+)\)\s+(#b[01]+|#x[0-9a-fA-F]+|\(_\s+bv\d+\s+\d+\))\)",
        output,
    ):
        values[name] = parse_bv_value(value)

    return values


def parse_bv_value(value):
    if value.startswith("#b"):
        return int(value[2:], 2)
    if value.startswith("#x"):
        return int(value[2:], 16)

    match = re.match(r"\(_\s+bv(\d+)\s+\d+\)", value)
    if match:
        return int(match.group(1))

    raise SolverBackendError(f"Cannot parse bit-vector model value: {value}")


def run_external_solver(solver, solver_name):
    solver_cmd = shutil.which(solver_name)
    if solver_cmd is None:
        raise SolverBackendError(
            f"Selected solver '{solver_name}' was not found on PATH. "
            f"Install the open-source {solver_name} binary and try again."
        )

    smt2 = solver.to_smt2()
    if "(get-model)" not in smt2:
        smt2 += "\n(get-model)\n"

    with tempfile.NamedTemporaryFile("w", suffix=".smt2", delete=False) as f:
        f.write(smt2)
        smt2_file = f.name

    if solver_name == "cvc5":
        cmd = [solver_cmd, "--produce-models", "--lang", "smt2", smt2_file]
    elif solver_name == "boolector":
        cmd = [solver_cmd, "--model-gen", "--smt2-model", smt2_file]
    else:
        cmd = [solver_cmd, smt2_file]

    try:
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=(SOLVER_TIMEOUT_MS // 1000) + 5,
            )
        except subprocess.TimeoutExpired as exc:
            raise SolverBackendError(
                f"Selected solver '{solver_name}' timed out."
            ) from exc
    finally:
        try:
            os.unlink(smt2_file)
        except OSError:
            pass

    combined_output = completed.stdout + completed.stderr
    first_line = completed.stdout.strip().splitlines()[0].strip() if completed.stdout.strip() else ""

    if completed.returncode != 0:
        raise SolverBackendError(
            f"Selected solver '{solver_name}' failed:\n{combined_output.strip()}"
        )

    if first_line != "sat":
        return False, None

    values = parse_solver_model(completed.stdout)
    if not values:
        raise SolverBackendError(
            f"Selected solver '{solver_name}' returned SAT but no parseable model."
        )

    return True, DictModelAdapter(values)


def validate_solver_backend(solver_name):
    if solver_name == "z3":
        return

    if shutil.which(solver_name) is None:
        raise SolverBackendError(
            f"Selected solver '{solver_name}' was not found on PATH. "
            f"Install the open-source {solver_name} binary and try again."
        )


def compute_lmin(jobs_data, messages_data):
    job_wcet     = {job["id"]: job["wcet_fullspeed"] for job in jobs_data}
    msg_receiver = {msg["id"]: msg["receiver"]       for msg in messages_data}
    msgs_sent_by = {}
    for msg in messages_data:
        msgs_sent_by.setdefault(msg["sender"], []).append(msg["id"])

    memo = {}
    def chain_min_time(job_id):
        if job_id in memo:
            return memo[job_id]
        outgoing_msgs = msgs_sent_by.get(job_id, [])
        if not outgoing_msgs:
            result = job_wcet[job_id]
        else:
            best_downstream = max(
                chain_min_time(msg_receiver[mid]) for mid in outgoing_msgs
            )
            result = job_wcet[job_id] + 1 + best_downstream
        memo[job_id] = result
        return result

    all_receivers = {msg["receiver"] for msg in messages_data}
    root_jobs     = [job["id"] for job in jobs_data if job["id"] not in all_receivers]
    if not root_jobs:
        root_jobs = [job["id"] for job in jobs_data]

    return max(max(chain_min_time(jid) for jid in root_jobs),
               max(job_wcet[jid] for jid in job_wcet))


def build_routing_options(sender_job, receiver_job):
    routing_options = []
    option_counter = 0

    sender_allowed = [
        rid for rid in jobs_data[sender_job]["can_run_on"]
        if rid in es_real_to_esidx
    ]

    receiver_allowed = [
        rid for rid in jobs_data[receiver_job]["can_run_on"]
        if rid in es_real_to_esidx
    ]

    for src_real in sender_allowed:

        for dst_real in receiver_allowed:

            src_es_idx = es_real_to_esidx[src_real]
            dst_es_idx = es_real_to_esidx[dst_real]

            path_key = (src_real, dst_real)

            if path_key not in path_data:
                continue

            for path in path_data[path_key]["paths"]:

                if not path:
                    continue

                path_nodes = [
                    node_to_idx[x]
                    for x in path
                ]

                routing_options.append(
                    (
                        option_counter,
                        src_es_idx,
                        dst_es_idx,
                        path_nodes
                    )
                )

                option_counter += 1

    return routing_options


def build_and_solve(T, solver_name):

    solver = Solver()
    if solver_name != "boolector":
        solver.set("timeout", SOLVER_TIMEOUT_MS)  # 5 minutes per SMT check

    use_bv = solver_name == "boolector"
    bv_width = max(
        8,
        (
            T
            + max(job["wcet_fullspeed"] for job in jobs_data)
            + num_nodes
            + num_msgs
            + 1
        ).bit_length() + 2,
    )

    def var(name):
        if use_bv:
            return BitVec(name, bv_width)
        return Int(name)

    def const(value):
        if use_bv:
            return BitVecVal(value, bv_width)
        return value

    def ult(left, right):
        if use_bv:
            return ULT(left, const(right) if isinstance(right, int) else right)
        return left < right

    def ule(left, right):
        if use_bv:
            return ULE(left, const(right) if isinstance(right, int) else right)
        return left <= right

    def uge(left, right):
        if use_bv:
            return UGE(left, const(right) if isinstance(right, int) else right)
        return left >= right

    # ============================================================
    # JOB VARIABLES
    # ============================================================

    job_assigned_es = [
        var(f"job_{i}_endsystem")
        for i in range(num_jobs)
    ]

    job_start_time = [
        var(f"job_{i}_start")
        for i in range(num_jobs)
    ]

    # ============================================================
    # MESSAGE VARIABLES
    # ============================================================

    #
    # NEW MODEL:
    #
    # Instead of msg_position[mid][tf]
    #
    # We model:
    #
    # hop_time[mid][hop]
    #
    # meaning:
    #
    # timeframe when message starts traversing hop
    #
    # This massively reduces SMT complexity.
    #

    msg_inject_time = [
        var(f"msg_{mid}_inject")
        for mid in range(num_msgs)
    ]

    msg_arrival_time = [
        var(f"msg_{mid}_arrival")
        for mid in range(num_msgs)
    ]

    msg_path_choice = [
        var(f"msg_{mid}_path_choice")
        for mid in range(num_msgs)
    ]

    #
    # Per-message hop timing variables
    #
    # hop_times[mid] = [ tf0, tf1, tf2 ... ]
    #

    hop_times = {}

    # ============================================================
    # JOB DOMAIN CONSTRAINTS
    # ============================================================

    for i, job in enumerate(jobs_data):

        allowed = [
            es_real_to_esidx[rid]
            for rid in job["can_run_on"]
            if rid in es_real_to_esidx
        ]

        solver.add(
            Or([
                job_assigned_es[i] == const(x)
                for x in allowed
            ])
        )

        wcet = job["wcet_fullspeed"]

        solver.add(uge(job_start_time[i], 0))
        solver.add(ule(job_start_time[i] + const(wcet), T))

    # ============================================================
    # CPU MUTUAL EXCLUSION
    # ============================================================

    for i in range(num_jobs):

        for j in range(i + 1, num_jobs):

            wcet_i = jobs_data[i]["wcet_fullspeed"]
            wcet_j = jobs_data[j]["wcet_fullspeed"]

            solver.add(
                Implies(
                    job_assigned_es[i] == job_assigned_es[j],
                    Or(
                        ule(job_start_time[i] + const(wcet_i), job_start_time[j]),
                        ule(job_start_time[j] + const(wcet_j), job_start_time[i])
                    )
                )
            )

    # ============================================================
    # STORE CANDIDATE EDGE USAGES
    # ============================================================

    #
    # edge_usage[(ni, nj)] = [(mid, rid, hop_time), ...]
    #
    # Later we add pairwise constraints:
    # if two selected route hops use the same edge, their hop times differ.
    #
    # This avoids creating one Boolean expression per edge per timeframe.
    #

    edge_usage = {}
    node_usage = {}

    # ============================================================
    # MESSAGE ROUTING
    # ============================================================

    for msg in messages_data:

        mid = msg["id"]

        sender_job = msg["sender"]
        receiver_job = msg["receiver"]

        sender_wcet = jobs_data[sender_job]["wcet_fullspeed"]

        # --------------------------------------------------------
        # COLLECT VALID ROUTES
        # --------------------------------------------------------

        routing_options = build_routing_options(sender_job, receiver_job)

        if not routing_options:
            return False, None

        # --------------------------------------------------------
        # path choice domain
        # --------------------------------------------------------

        solver.add(
            Or([
                msg_path_choice[mid] == const(rid)
                for (rid, _, _, _) in routing_options
            ])
        )

        # --------------------------------------------------------
        # injection constraints
        # --------------------------------------------------------

        solver.add(
            uge(
                msg_inject_time[mid],
                job_start_time[sender_job] + const(sender_wcet),
            )
        )
        solver.add(uge(msg_inject_time[mid], 0))
        solver.add(ult(msg_inject_time[mid], T))

        solver.add(uge(msg_arrival_time[mid], 0))
        solver.add(ult(msg_arrival_time[mid], T))

        # --------------------------------------------------------
        # ROUTING CASES
        # --------------------------------------------------------

        routing_cases = []

        for (
            rid,
            src_es_idx,
            dst_es_idx,
            path_nodes
        ) in routing_options:

            conds = []

            # ----------------------------------------------------
            # assignment consistency
            # ----------------------------------------------------

            conds.append(
                job_assigned_es[sender_job]
                ==
                const(src_es_idx)
            )

            conds.append(
                job_assigned_es[receiver_job]
                ==
                const(dst_es_idx)
            )

            conds.append(
                msg_path_choice[mid] == const(rid)
            )

            # ----------------------------------------------------
            # PATH
            # ----------------------------------------------------

            num_hops = len(path_nodes) - 1

            #
            # create hop timing vars
            #

            local_hop_times = []

            for hop in range(num_hops):

                hvar = var(f"msg_{mid}_hop_{rid}_{hop}")

                local_hop_times.append(hvar)

                solver.add(uge(hvar, 0))
                solver.add(ult(hvar, T))

            hop_times[(mid, rid)] = local_hop_times

            # ----------------------------------------------------
            # first hop starts at inject time
            # ----------------------------------------------------

            if num_hops > 0:

                conds.append(
                    local_hop_times[0]
                    ==
                    msg_inject_time[mid]
                )

            # ----------------------------------------------------
            # hops ordered
            # ----------------------------------------------------

            #
            # each hop takes 1 timeframe
            #
            # but may wait if wire busy
            #

            for h in range(num_hops - 1):

                conds.append(
                    uge(
                        local_hop_times[h + 1],
                        local_hop_times[h] + const(1)
                    )
                )

            # ----------------------------------------------------
            # arrival time
            # ----------------------------------------------------

            if num_hops > 0:

                conds.append(
                    msg_arrival_time[mid]
                    ==
                    local_hop_times[-1] + const(1)
                )

            else:

                conds.append(
                    msg_arrival_time[mid]
                    ==
                    msg_inject_time[mid]
                )

            # ----------------------------------------------------
            # receiver waits
            # ----------------------------------------------------

            conds.append(
                uge(job_start_time[receiver_job], msg_arrival_time[mid])
            )

            # ----------------------------------------------------
            # REGISTER EDGE USAGE
            # ----------------------------------------------------

            for h in range(num_hops):

                ni = path_nodes[h]
                nj = path_nodes[h + 1]

                edge = (
                    min(ni, nj),
                    max(ni, nj)
                )

                if edge not in edge_usage:
                    edge_usage[edge] = []

                edge_usage[edge].append(
                    (
                        mid,
                        rid,
                        local_hop_times[h]
                    )
                )

            # ----------------------------------------------------
            # REGISTER NODE OCCUPANCY
            # ----------------------------------------------------

            #
            # A message occupies:
            # - the source node at inject time,
            # - each intermediate node from arrival until next departure,
            # - the destination node at arrival time.
            #
            # Intervals are inclusive over integer timeframes.
            #

            if num_hops == 0:

                node_intervals = [
                    (
                        path_nodes[0],
                        msg_inject_time[mid],
                        msg_inject_time[mid]
                    )
                ]

            else:

                node_intervals = [
                    (
                        path_nodes[0],
                        msg_inject_time[mid],
                        local_hop_times[0]
                    )
                ]

                for node_pos in range(1, len(path_nodes) - 1):

                    node_intervals.append(
                        (
                        path_nodes[node_pos],
                        local_hop_times[node_pos - 1] + const(1),
                        local_hop_times[node_pos]
                    )
                    )

                node_intervals.append(
                    (
                        path_nodes[-1],
                        msg_arrival_time[mid],
                        msg_arrival_time[mid]
                    )
                )

            for node_idx, start_expr, end_expr in node_intervals:

                if node_idx not in node_usage:
                    node_usage[node_idx] = []

                node_usage[node_idx].append(
                    (
                        mid,
                        rid,
                        start_expr,
                        end_expr
                    )
                )

            routing_cases.append(
                And(conds)
            )

        solver.add(
            Or(routing_cases)
        )

    # ============================================================
    # WIRE CONTENTION
    # ============================================================

    for key, users in edge_usage.items():

        for i in range(len(users)):

            mid_i, rid_i, hop_time_i = users[i]

            for j in range(i + 1, len(users)):

                mid_j, rid_j, hop_time_j = users[j]

                if mid_i == mid_j:
                    continue

                solver.add(
                    Implies(
                        And(
                            msg_path_choice[mid_i] == const(rid_i),
                            msg_path_choice[mid_j] == const(rid_j)
                        ),
                        hop_time_i != hop_time_j
                    )
                )

    # ============================================================
    # NODE CONTENTION
    # ============================================================

    for key, users in node_usage.items():

        for i in range(len(users)):

            mid_i, rid_i, start_i, end_i = users[i]

            for j in range(i + 1, len(users)):

                mid_j, rid_j, start_j, end_j = users[j]

                if mid_i == mid_j:
                    continue

                solver.add(
                    Implies(
                        And(
                            msg_path_choice[mid_i] == const(rid_i),
                            msg_path_choice[mid_j] == const(rid_j)
                        ),
                        Or(
                            ult(end_i, start_j),
                            ult(end_j, start_i)
                        )
                    )
                )

    # ============================================================
    # SOLVE
    # ============================================================

    if solver_name != "z3":
        return run_external_solver(solver, solver_name)

    result = solver.check()

    if result != sat:
        return False, None

    return True, Z3ModelAdapter(solver.model())

def try_T(T, solver_name):

    feasible, model = build_and_solve(T, solver_name)

    if not feasible:
        return T, False, None

    # ============================================================
    # JOB INFO
    # ============================================================

    job_info = {}
    job_dependencies = {
        job["id"]: sorted({
            msg["sender"]
            for msg in messages_data
            if msg["receiver"] == job["id"]
        })
        for job in jobs_data
    }

    for i, job in enumerate(jobs_data):

        es_idx = model.value(f"job_{i}_endsystem")

        real_node = endsystems[es_idx]

        start_time = model.value(f"job_{i}_start")

        wcet = job["wcet_fullspeed"]

        job_info[job["id"]] = {
            "job_id": job["id"],
            "assigned_node": real_node,
            "start_time": start_time,
            "finish_time": start_time + wcet,
            "wcet": wcet,
            "dependencies": job_dependencies[job["id"]],
        }

    # ============================================================
    # MESSAGE INFO
    # ============================================================

    msg_details = []

    for msg in messages_data:

        mid = msg["id"]

        sender_job = msg["sender"]
        receiver_job = msg["receiver"]

        sender_node = job_info[sender_job]["assigned_node"]
        receiver_node = job_info[receiver_job]["assigned_node"]

        inject_tf = model.value(f"msg_{mid}_inject")

        arrival_tf = model.value(f"msg_{mid}_arrival")

        chosen_rid = model.value(f"msg_{mid}_path_choice")

        routing_options = build_routing_options(sender_job, receiver_job)
        chosen_path_nodes = None

        for rid, _, _, path_nodes in routing_options:

            if rid == chosen_rid:
                chosen_path_nodes = path_nodes
                break

        if chosen_path_nodes is None:
            return T, False, None

        hop_schedule = []

        hop_idx = 0

        while True:

            var_name = f"msg_{mid}_hop_{chosen_rid}_{hop_idx}"

            val = model.maybe_value(var_name)

            if val is None:
                break

            hop_schedule.append(val)

            hop_idx += 1

        path_timeline = []

        if chosen_path_nodes:

            path_timeline.append(
                {
                    "node": idx_to_node[chosen_path_nodes[0]],
                    "timeframe": inject_tf
                }
            )

            for hop_idx, hop_tf in enumerate(hop_schedule):

                path_timeline.append(
                    {
                        "node": idx_to_node[chosen_path_nodes[hop_idx + 1]],
                        "timeframe": hop_tf + 1
                    }
                )

        msg_details.append({

            "msg_id": mid,

            "sender_job": sender_job,
            "receiver_job": receiver_job,

            "sender_node": sender_node,
            "receiver_node": receiver_node,

            "inject_timeframe": inject_tf,
            "arrive_timeframe": arrival_tf,

            "path_choice": chosen_rid,

            "hop_times": path_timeline
        })

    schedule = {
        "jobs": list(job_info.values()),
        "messages": msg_details,
    }

    return T, True, schedule

def parse_args():
    parser = argparse.ArgumentParser(
        description="SMT-based scheduler for distributed time-sensitive networks."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILE,
        help=f"Scheduler input JSON file. Default: {DEFAULT_INPUT_FILE}.",
    )
    parser.add_argument(
        "--input-glob",
        action="append",
        help=(
            "Glob pattern for running multiple scheduler input JSON files, "
            "for example 'input/cloud2_*.json'. Can be passed more than once."
        ),
    )
    parser.add_argument(
        "--context",
        help=(
            "Context model JSON whose generated_inputs should be scheduled. "
            "Use util/context_model.py to create processor-failure contexts."
        ),
    )
    parser.add_argument(
        "--solver",
        choices=SUPPORTED_SOLVERS,
        default="z3",
        help="SMT solver backend to use. Default: z3.",
    )
    return parser.parse_args()


def solve_input_file(input_path, solver_name):
    load_input(input_path)

    l_min = compute_lmin(jobs_data, messages_data)
    t_max = app_deadline

    SEARCH_LOWER_BOUND = l_min

    low = max(l_min, SEARCH_LOWER_BOUND)
    high = t_max
    best_schedule = None
    optimal_T     = None

    print(f"Selected solver: {solver_name}")
    print(f"Search range: T = {low} to {high}")

    while low <= high:
        mid = (low + high) // 2
        print(f"Trying T = {mid}...")

        try:
            T_val, feasible, schedule = try_T(mid, solver_name)
        except SolverBackendError as exc:
            print(f"\nSolver backend error: {exc}")
            return 2

        if feasible:
            print(f"  SAT at T = {T_val}")
            optimal_T = T_val
            best_schedule = schedule
            high = mid - 1
        else:
            print(f"  UNSAT/UNKNOWN at T = {T_val}")
            low = mid + 1

    if best_schedule is not None:
        print(f"\nOptimal T = {optimal_T} -- stopping search.")

        output = {
            "solver":           solver_name,
            "input_file":       input_file,
            "optimal_makespan": optimal_T,
            "schedule":         best_schedule,
        }
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        suffix = "smt_output" if solver_name == "z3" else f"{solver_name}_smt_output"
        os.makedirs("output", exist_ok=True)
        output_file = f"output/{base_name}_{suffix}.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=4)
        print(f"Schedule written to {output_file}")

        # ── Pretty-print summary to console ──
        print(f"\n{'='*60}")
        print(f"Optimal makespan T = {optimal_T}")
        print(f"{'='*60}")

        print("\nJOB SCHEDULE:")
        for job in best_schedule["jobs"]:
            print(f"  Job {job['job_id']:>2} | node {job['assigned_node']:>3} | "
                  f"start={job['start_time']:>3}  finish={job['finish_time']:>3}  wcet={job['wcet']}")

        print("\nMESSAGE DETAILS:")
        for msg in best_schedule["messages"]:
            print(f"  Msg {msg['msg_id']:>2} | {msg['sender_node']} -> {msg['receiver_node']} | "
                  f"inject@tf={msg['inject_timeframe']}  arrive@tf={msg['arrive_timeframe']}")

      

    else:
        print("No feasible schedule exists within the application deadline.")
        return 1

    return 0


def main():
    args = parse_args()
    solver_name = args.solver

    try:
        validate_solver_backend(solver_name)
    except SolverBackendError as exc:
        print(f"\nSolver backend error: {exc}")
        return 2

    input_files = []
    if args.context:
        with open(args.context, "r") as f:
            context_model = json.load(f)
        input_files = context_model.get("generated_inputs", [])
        if not input_files:
            print("Context model does not contain any generated_inputs.")
            return 1
    elif args.input_glob:
        for pattern in args.input_glob:
            input_files.extend(sorted(glob.glob(pattern)))
        input_files = sorted(dict.fromkeys(input_files))
        if not input_files:
            print("No input files matched --input-glob pattern(s).")
            return 1
    else:
        input_files = [args.input]

    exit_code = 0
    for idx, input_path in enumerate(input_files, start=1):
        if len(input_files) > 1:
            print(f"\n{'#'*60}")
            print(f"Scheduling input {idx}/{len(input_files)}: {input_path}")
            print(f"{'#'*60}")

        result = solve_input_file(input_path, solver_name)
        if result != 0:
            exit_code = result

    return exit_code


# ── CRITICAL: all execution must be inside this guard on Windows ──
if __name__ == "__main__":
    raise SystemExit(main())

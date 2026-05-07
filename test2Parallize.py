# import json
# import concurrent.futures
# from z3 import *
# from util.KPathFinding2 import compute_k_paths

# # ── module-level setup (safe to run in workers too) ──
# input_file = "input/graph_0.json"
# with open(input_file, "r") as f:
#     data = json.load(f)

# jobs_data      = data["application"]["jobs"]
# messages_data  = data["application"]["messages"]
# platform_nodes = data["platform"]["nodes"]
# app_deadline   = data["application"]["deadline"]

# endsystems = sorted([n["id"] for n in platform_nodes if not n["is_router"]])
# switches   = sorted([n["id"] for n in platform_nodes if     n["is_router"]])
# all_nodes  = endsystems + switches

# num_endsystems = len(endsystems)
# num_switches   = len(switches)
# num_nodes      = len(all_nodes)

# node_to_idx      = {real_id: idx for idx, real_id in enumerate(all_nodes)}
# idx_to_node      = {idx: real_id for real_id, idx  in node_to_idx.items()}
# es_real_to_esidx = {real_id: i   for i, real_id    in enumerate(endsystems)}

# adj = [[False] * num_nodes for _ in range(num_nodes)]
# for link in data["platform"].get("links", []):
#     i = node_to_idx[link["start"]]
#     j = node_to_idx[link["end"]]
#     adj[i][j] = True
#     adj[j][i] = True

# undirected_links = set()
# for ni in range(num_nodes):
#     for nj in range(num_nodes):
#         if adj[ni][nj]:
#             undirected_links.add((min(ni, nj), max(ni, nj)))

# path_data = compute_k_paths(input_file, k=2)
# num_jobs  = len(jobs_data)
# num_msgs  = len(messages_data)


# def compute_lmin(jobs_data, messages_data):
#     job_wcet     = {job["id"]: job["wcet_fullspeed"] for job in jobs_data}
#     msg_receiver = {msg["id"]: msg["receiver"]       for msg in messages_data}
#     msgs_sent_by = {}
#     for msg in messages_data:
#         msgs_sent_by.setdefault(msg["sender"], []).append(msg["id"])

#     memo = {}
#     def chain_min_time(job_id):
#         if job_id in memo:
#             return memo[job_id]
#         outgoing_msgs = msgs_sent_by.get(job_id, [])
#         if not outgoing_msgs:
#             result = job_wcet[job_id]
#         else:
#             best_downstream = max(
#                 chain_min_time(msg_receiver[mid]) for mid in outgoing_msgs
#             )
#             result = job_wcet[job_id] + 1 + best_downstream
#         memo[job_id] = result
#         return result

#     all_receivers = {msg["receiver"] for msg in messages_data}
#     root_jobs     = [job["id"] for job in jobs_data if job["id"] not in all_receivers]
#     if not root_jobs:
#         root_jobs = [job["id"] for job in jobs_data]

#     return max(max(chain_min_time(jid) for jid in root_jobs),
#                max(job_wcet[jid] for jid in job_wcet))


# def build_and_solve(T):
#     solver = Solver()

#     job_assigned_es = [Int(f"job_{i}_endsystem") for i in range(num_jobs)]
#     job_start_time  = [Int(f"job_{i}_start")     for i in range(num_jobs)]

#     node_occupied_by = [
#         [Int(f"node_{ni}_at_tf_{tf}") for tf in range(T)]
#         for ni in range(num_nodes)
#     ]

#     wire_in_use = {}
#     for tf in range(T):
#         for (ni, nj) in undirected_links:
#             wire_in_use[(tf, ni, nj)] = Bool(f"wire_{ni}_{nj}_at_tf_{tf}")

#     msg_has_arrived = [
#         [Int(f"msg_{mid}_arrived_by_tf_{tf}") for tf in range(T)]
#         for mid in range(num_msgs)
#     ]

#     for ni in range(num_nodes):
#         for tf in range(T):
#             solver.add(node_occupied_by[ni][tf] >= 0)
#             solver.add(node_occupied_by[ni][tf] <= num_msgs)

#     for mid in range(num_msgs):
#         for tf in range(T):
#             solver.add(Or(
#                 msg_has_arrived[mid][tf] == 0,
#                 msg_has_arrived[mid][tf] == mid + 1
#             ))
#         solver.add(msg_has_arrived[mid][0] == 0)
#         for tf in range(T - 1):
#             solver.add(Implies(
#                 msg_has_arrived[mid][tf] == mid + 1,
#                 msg_has_arrived[mid][tf + 1] == mid + 1
#             ))

#     for i, job in enumerate(jobs_data):
#         allowed = [
#             es_real_to_esidx[rid]
#             for rid in job["can_run_on"]
#             if rid in es_real_to_esidx
#         ]
#         solver.add(Or([job_assigned_es[i] == k for k in allowed]))

#     for i, job in enumerate(jobs_data):
#         wcet = job["wcet_fullspeed"]
#         solver.add(job_start_time[i] >= 0)
#         solver.add(job_start_time[i] + wcet <= T)

#     for i in range(num_jobs):
#         for j in range(i + 1, num_jobs):
#             wcet_i = jobs_data[i]["wcet_fullspeed"]
#             wcet_j = jobs_data[j]["wcet_fullspeed"]
#             solver.add(Implies(
#                 job_assigned_es[i] == job_assigned_es[j],
#                 Or(
#                     job_start_time[i] + wcet_i <= job_start_time[j],
#                     job_start_time[j] + wcet_j <= job_start_time[i]
#                 )
#             ))

#     for msg in messages_data:
#         mid0 = msg["id"]
#         mid1 = msg["id"] + 1
#         sender_job_idx   = msg["sender"]
#         receiver_job_idx = msg["receiver"]
#         sender_wcet      = jobs_data[sender_job_idx]["wcet_fullspeed"]

#         all_options = []
#         for src_es_idx, src_es_real in enumerate(endsystems):
#             for dst_es_idx, dst_es_real in enumerate(endsystems):
#                 if src_es_idx == dst_es_idx:
#                     continue
#                 path_key = (src_es_real, dst_es_real)
#                 if path_key not in path_data:
#                     continue
#                 for path in path_data[path_key]["paths"]:
#                     num_hops = len(path)
#                     for inj_tf in range(T):
#                         arrival_tf = inj_tf + num_hops - 1
#                         if arrival_tf >= T:
#                             continue
#                         conds = [
#                             job_assigned_es[sender_job_idx]   == src_es_idx,
#                             job_assigned_es[receiver_job_idx] == dst_es_idx,
#                             job_start_time[sender_job_idx] + sender_wcet <= inj_tf,
#                         ]
#                         for step, real_node_id in enumerate(path):
#                             ni = node_to_idx[real_node_id]
#                             conds.append(node_occupied_by[ni][inj_tf + step] == mid1)
#                         for step in range(len(path) - 1):
#                             ni = node_to_idx[path[step]]
#                             nj = node_to_idx[path[step + 1]]
#                             lk = (inj_tf + step, min(ni, nj), max(ni, nj))
#                             if lk in wire_in_use:
#                                 conds.append(wire_in_use[lk])
#                         conds.append(msg_has_arrived[mid0][arrival_tf] == mid1)
#                         conds.append(job_start_time[receiver_job_idx] >= arrival_tf + 1)
#                         all_options.append(And(conds))

#         if all_options:
#             solver.add(Or(all_options))
#         else:
#             solver.add(BoolVal(False))
#             return False, None

#     result = solver.check()
#     if result == sat:
#         return True, solver.model()
#     return False, None


# def try_T(T):
#     """
#     Worker function. Returns plain Python data only — no Z3 objects.
#     Z3 Model objects cannot be pickled across process boundaries on Windows.
#     """
#     feasible, model = build_and_solve(T)
#     if not feasible:
#         return T, False, None

#     # Extract all values from the model HERE, inside the worker process
#     schedule = []
#     for i, job in enumerate(jobs_data):
#         es_idx     = model[Int(f"job_{i}_endsystem")].as_long()
#         real_node  = endsystems[es_idx]
#         start_time = model[Int(f"job_{i}_start")].as_long()
#         wcet       = job["wcet_fullspeed"]
#         schedule.append({
#             "job_id":        job["id"],
#             "assigned_node": real_node,
#             "start_time":    start_time,
#             "finish_time":   start_time + wcet,
#             "wcet":          wcet,
#         })

#     return T, True, schedule  # plain dict — picklable


# # ── CRITICAL: all execution must be inside this guard on Windows ──
# if __name__ == "__main__":
#     l_min = compute_lmin(jobs_data, messages_data)
#     t_max = app_deadline

#     print(f"Search range: T = {l_min} to {t_max}")

#     NUM_WORKERS  = 15
#     best_schedule = None
#     optimal_T     = None
#     T_range       = list(range(92, t_max + 1))

#     with concurrent.futures.ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
#         for batch_start in range(0, len(T_range), NUM_WORKERS):
#             batch = T_range[batch_start : batch_start + NUM_WORKERS]
#             print(f"Trying T = {batch[0]} .. {batch[-1]} in parallel...")

#             future_to_T = {executor.submit(try_T, T): T for T in batch}
#             sat_results = []

#             for future in concurrent.futures.as_completed(future_to_T):
#                 T_val, feasible, schedule = future.result()
#                 if feasible:
#                     print(f"  SAT at T = {T_val}")
#                     sat_results.append((T_val, schedule))
#                 else:
#                     print(f"  UNSAT at T = {T_val}")

#             if sat_results:
#                 optimal_T, best_schedule = min(sat_results, key=lambda x: x[0])
#                 print(f"\nOptimal T = {optimal_T} — stopping search.")
#                 break

#     if best_schedule is not None:
#         output = {
#             "optimal_makespan": optimal_T,
#             "schedule": best_schedule,
#         }
#         base_name   = input_file.replace("input/", "").replace(".json", "")
#         output_file = f"output/{base_name}_smt_output.json"
#         with open(output_file, "w") as f:
#             json.dump(output, f, indent=4)
#         print(f"Schedule written to {output_file}")
#     else:
#         print("No feasible schedule exists within the application deadline.")


import json
import concurrent.futures
from z3 import *
from util.KPathFinding2 import compute_k_paths

# ── module-level setup (safe to run in workers too) ──
input_file = "input/graph_0.json"
with open(input_file, "r") as f:
    data = json.load(f)

jobs_data      = data["application"]["jobs"]
messages_data  = data["application"]["messages"]
platform_nodes = data["platform"]["nodes"]
app_deadline   = data["application"]["deadline"]

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


def build_and_solve(T):
    solver = Solver()

    job_assigned_es = [Int(f"job_{i}_endsystem") for i in range(num_jobs)]
    job_start_time  = [Int(f"job_{i}_start")     for i in range(num_jobs)]

    node_occupied_by = [
        [Int(f"node_{ni}_at_tf_{tf}") for tf in range(T)]
        for ni in range(num_nodes)
    ]

    wire_in_use = {}
    for tf in range(T):
        for (ni, nj) in undirected_links:
            wire_in_use[(tf, ni, nj)] = Bool(f"wire_{ni}_{nj}_at_tf_{tf}")

    msg_has_arrived = [
        [Int(f"msg_{mid}_arrived_by_tf_{tf}") for tf in range(T)]
        for mid in range(num_msgs)
    ]

    for ni in range(num_nodes):
        for tf in range(T):
            solver.add(node_occupied_by[ni][tf] >= 0)
            solver.add(node_occupied_by[ni][tf] <= num_msgs)

    for mid in range(num_msgs):
        for tf in range(T):
            solver.add(Or(
                msg_has_arrived[mid][tf] == 0,
                msg_has_arrived[mid][tf] == mid + 1
            ))
        solver.add(msg_has_arrived[mid][0] == 0)
        for tf in range(T - 1):
            solver.add(Implies(
                msg_has_arrived[mid][tf] == mid + 1,
                msg_has_arrived[mid][tf + 1] == mid + 1
            ))

    for i, job in enumerate(jobs_data):
        allowed = [
            es_real_to_esidx[rid]
            for rid in job["can_run_on"]
            if rid in es_real_to_esidx
        ]
        solver.add(Or([job_assigned_es[i] == k for k in allowed]))

    for i, job in enumerate(jobs_data):
        wcet = job["wcet_fullspeed"]
        solver.add(job_start_time[i] >= 0)
        solver.add(job_start_time[i] + wcet <= T)

    for i in range(num_jobs):
        for j in range(i + 1, num_jobs):
            wcet_i = jobs_data[i]["wcet_fullspeed"]
            wcet_j = jobs_data[j]["wcet_fullspeed"]
            solver.add(Implies(
                job_assigned_es[i] == job_assigned_es[j],
                Or(
                    job_start_time[i] + wcet_i <= job_start_time[j],
                    job_start_time[j] + wcet_j <= job_start_time[i]
                )
            ))

    for msg in messages_data:
        mid0 = msg["id"]
        mid1 = msg["id"] + 1
        sender_job_idx   = msg["sender"]
        receiver_job_idx = msg["receiver"]
        sender_wcet      = jobs_data[sender_job_idx]["wcet_fullspeed"]

        all_options = []
        for src_es_idx, src_es_real in enumerate(endsystems):
            for dst_es_idx, dst_es_real in enumerate(endsystems):
                if src_es_idx == dst_es_idx:
                    continue
                path_key = (src_es_real, dst_es_real)
                if path_key not in path_data:
                    continue
                for path in path_data[path_key]["paths"]:
                    num_hops = len(path)
                    for inj_tf in range(T):
                        arrival_tf = inj_tf + num_hops - 1
                        if arrival_tf >= T:
                            continue
                        conds = [
                            job_assigned_es[sender_job_idx]   == src_es_idx,
                            job_assigned_es[receiver_job_idx] == dst_es_idx,
                            job_start_time[sender_job_idx] + sender_wcet <= inj_tf,
                        ]
                        for step, real_node_id in enumerate(path):
                            ni = node_to_idx[real_node_id]
                            conds.append(node_occupied_by[ni][inj_tf + step] == mid1)
                        for step in range(len(path) - 1):
                            ni = node_to_idx[path[step]]
                            nj = node_to_idx[path[step + 1]]
                            lk = (inj_tf + step, min(ni, nj), max(ni, nj))
                            if lk in wire_in_use:
                                conds.append(wire_in_use[lk])
                        conds.append(msg_has_arrived[mid0][arrival_tf] == mid1)
                        conds.append(job_start_time[receiver_job_idx] >= arrival_tf + 1)
                        all_options.append(And(conds))

        if all_options:
            solver.add(Or(all_options))
        else:
            solver.add(BoolVal(False))
            return False, None

    result = solver.check()
    if result == sat:
        return True, solver.model()
    return False, None


def try_T(T):
    """
    Worker function. Extracts all data from Z3 model inside the worker
    so only plain picklable Python objects are returned.
    """
    feasible, model = build_and_solve(T)
    if not feasible:
        return T, False, None

    # ── Extract job assignments and timings ──
    job_info = {}
    for i, job in enumerate(jobs_data):
        es_idx     = model[Int(f"job_{i}_endsystem")].as_long()
        real_node  = endsystems[es_idx]
        start_time = model[Int(f"job_{i}_start")].as_long()
        wcet       = job["wcet_fullspeed"]
        job_info[job["id"]] = {
            "job_id":        job["id"],
            "assigned_node": real_node,
            "start_time":    start_time,
            "finish_time":   start_time + wcet,
            "wcet":          wcet,
        }

    # ── Read node_occupied_by values from model ──
    # node_occupied_by[ni][tf] == mid+1 means message mid is at node ni at tf
    node_occupied = {}
    for ni in range(num_nodes):
        for tf in range(T):
            val = model[Int(f"node_{ni}_at_tf_{tf}")]
            if val is not None:
                v = val.as_long()
                if v > 0:
                    node_occupied[(ni, tf)] = v - 1  # convert to 0-indexed msg id

    # ── Read wire_in_use values from model ──
    wire_active = set()
    for tf in range(T):
        for (ni, nj) in undirected_links:
            val = model[Bool(f"wire_{ni}_{nj}_at_tf_{tf}")]
            if val is not None and is_true(val):
                wire_active.add((tf, ni, nj))

    # ── Build per-message transmission details ──
    msg_details = []
    for msg in messages_data:
        mid = msg["id"]
        sender_job   = job_info[msg["sender"]]
        receiver_job = job_info[msg["receiver"]]

        # Collect all (tf -> ni) where this message appears in the model
        msg_positions = {}
        for (ni, tf), m in node_occupied.items():
            if m == mid:
                msg_positions[tf] = ni

        if msg_positions:
            inj_tf     = min(msg_positions.keys())
            arrival_tf = max(msg_positions.keys())
        else:
            inj_tf     = None
            arrival_tf = None

        msg_details.append({
            "msg_id":           mid,
            "sender_job":       msg["sender"],
            "receiver_job":     msg["receiver"],
            "sender_node":      sender_job["assigned_node"],
            "receiver_node":    receiver_job["assigned_node"],
            "inject_timeframe": inj_tf,
            "arrive_timeframe": arrival_tf,
            # real node id at each timeframe while in transit
            "positions":        {tf: idx_to_node[ni] for tf, ni in msg_positions.items()},
        })


    # ── Assemble final schedule ──
    schedule = {
        "jobs":          list(job_info.values()),
        "messages":      msg_details,
        
    }
    return T, True, schedule

# ── CRITICAL: all execution must be inside this guard on Windows ──
if __name__ == "__main__":
    l_min = compute_lmin(jobs_data, messages_data)
    t_max = app_deadline

    print(f"Search range: T = {l_min} to {t_max}")

    NUM_WORKERS   = 16
    best_schedule = None
    optimal_T     = None
    T_range       = list(range(l_min, t_max + 1))

    with concurrent.futures.ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        for batch_start in range(0, len(T_range), NUM_WORKERS):
            batch = T_range[batch_start : batch_start + NUM_WORKERS]
            print(f"Trying T = {batch[0]} .. {batch[-1]} in parallel...")

            future_to_T = {executor.submit(try_T, T): T for T in batch}
            sat_results = []

            for future in concurrent.futures.as_completed(future_to_T):
                T_val, feasible, schedule = future.result()
                if feasible:
                    print(f"  SAT at T = {T_val}")
                    sat_results.append((T_val, schedule))
                else:
                    print(f"  UNSAT at T = {T_val}")

            if sat_results:
                optimal_T, best_schedule = min(sat_results, key=lambda x: x[0])
                print(f"\nOptimal T = {optimal_T} — stopping search.")
                break

    if best_schedule is not None:
        output = {
            "optimal_makespan": optimal_T,
            "schedule":         best_schedule,
        }
        base_name   = input_file.replace("input/", "").replace(".json", "")
        output_file = f"output/{base_name}_smt_output.json"
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
# SMT-Based Scheduler for Distributed Time-Sensitive Networks

This project implements an **SMT-based scheduler** for distributed time-sensitive networks.  
It takes a system model consisting of an application DAG and network topology, and computes a feasible schedule that satisfies timing constraints such as deadlines and dependencies.

The scheduler supports both:
- Automatically generated workloads (via TGFF)
- Manually defined input JSON graphs

---

# Project Structure

test_code/
│
├── input/                  # Input workload files (JSON)
│   ├── graph_0.json
│   ├── graph_1.json
│   └── ...
│
├── output/                 # Generated SMT scheduling outputs
│   └── *_smt_output.json
│
├── misc/                   # Legacy / unused scripts and older scheduler versions
│
├── prevSchedules/         # Previously generated schedules using older schedulers
│
├── util/                  # Utility modules used by main scheduler
│   ├── compute_min.py      # Computes minimum execution time / critical path
│   ├── KPathFinding.py     # Computes K paths (cost-only version)
│   ├── KPathFinding2.py    # Computes K paths with full path info (currently used)
│
├── __pycache__/
│
├── test2.py
├── test2Parallize.py      # Main scheduler (recommended entry point)
├── schedule_output_paper_model.json
└── README.md

---

# Input Format

The `input/` folder contains JSON files describing:

- Application DAG (tasks + dependencies)
- Network topology
- Communication edges between tasks

Each file represents a full scheduling problem instance.

Example:
input/graph_3.json

---

# Output Format

For an input file:
input/graph_3.json

Output is generated as:
output/graph_3_smt_output.json

Logic:
base_name = input_file.replace("input/", "").replace(".json", "")
output_file = f"output/{base_name}_smt_output.json"

---

# Utility Modules

## compute_min.py
Computes minimum execution time / critical path of DAG.

## KPathFinding.py
- Finds K paths between nodes
- Returns cost-only results

## KPathFinding2.py (currently used)
- Improved version
- Returns full path + cost
- Used in scheduler routing logic

---

# How to Run

There are 2 ways to use this system.

---

# Option 1: Use Existing Input Files

Run:

python test2Parallize.py

By default, it runs on:
input/graph_0.json

To change input file:
Edit inside test2Parallize.py:

input_file = "input/graph_1.json"

Notes:
- Some inputs require a "deadline" field in the JSON

---

# Option 2: Generate Your Own Input Files (TGFF Pipeline)

We use TGFF (Task Graphs For Free) to generate application graphs.

---

## Step 1: Create TGFF config

Example:
simple.tgffopt

Refer to TGFF manual for syntax.

---

## Step 2: Run TGFF (Ubuntu recommended)

Important: do NOT pass extension.

./tgff examples/simple

This generates:
examples/simple.tgff

---

## Step 3: Convert TGFF → JSON

Run:

python parsetgff.py examples/simple.tgff

This will:
- Parse TGFF file
- Convert into JSON format
- Save output into input/ folder

---

# Important Notes

- TGFF generates ONLY the application model
- Network model is currently hardcoded in parsetgff.py
- Deadline field is manually added in post-processing

---

# Workflow Overview

TGFF (.tgffopt)
    ↓
tgff tool generates .tgff
    ↓
parsetgff.py converts to JSON
    ↓
input/*.json
    ↓
test2Parallize.py (SMT Scheduler)
    ↓
output/*.json

---

# Key Features

- SMT-based scheduling engine
- DAG dependency handling
- K-shortest path routing
- Parallel scheduling support
- TGFF-based synthetic workload generation

---

# Future Improvements

- Auto deadline generation
- Dynamic network model parsing
- Replace hardcoded platform model
- Better TGFF integration pipeline
- Visualization of schedules

---

# Author Notes

This project is designed for research in:
- Real-time systems
- Distributed scheduling
- SMT optimization
- Task graph scheduling
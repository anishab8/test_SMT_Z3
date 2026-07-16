import copy
import json
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
CONTEXT_FILE = ROOT_DIR / "context" / "context.json"
BASE_INPUT_FILE = ROOT_DIR / "input" / "graph_1 - input.json"
EVENT_INPUT_DIR = ROOT_DIR / "input"
EVENT_SCHEDULE_DIR = ROOT_DIR / "prevSchedules" / "contextSchedules"
SCHEDULER_FILE = ROOT_DIR / "test2Parallize.py"


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


def event_file_stem(event):
    event_type = event.get("event_type", "event")
    event_id = event.get("event_id", "unknown")
    return f"{event_type}_{event_id}"


def remove_router_from_input(input_data, router_id):
    updated_data = copy.deepcopy(input_data)
    platform = updated_data["platform"]

    platform["nodes"] = [
        node
        for node in platform.get("nodes", [])
        if str(node.get("id")) != router_id
    ]

    platform["links"] = [
        link
        for link in platform.get("links", [])
        if str(link.get("start")) != router_id and str(link.get("end")) != router_id
    ]

    return updated_data


def run_scheduler(input_path, schedule_path):
    EVENT_SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            sys.executable,
            str(SCHEDULER_FILE),
            str(input_path),
            "--output-file",
            str(schedule_path),
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
    )

    return result.returncode == 0 and schedule_path.exists(), result


def process_rout_failure(event, base_input_data):
    router_id = str(event["node_id"])
    stem = event_file_stem(event)
    input_name = f"{stem}_input.json"
    schedule_name = f"{stem}_schedule.json"

    event_input_path = EVENT_INPUT_DIR / input_name
    event_schedule_path = EVENT_SCHEDULE_DIR / schedule_name

    updated_input = remove_router_from_input(base_input_data, router_id)
    save_json(event_input_path, updated_input)

    sat, result = run_scheduler(event_input_path, event_schedule_path)

    event["event_input"] = input_name
    event["event_schedule"] = schedule_name
    event["SAT"] = sat

    if not sat:
        event["scheduler_returncode"] = result.returncode
        if result.stderr:
            event["scheduler_error"] = result.stderr.strip()

    return event


EVENT_PROCESSORS = {
    "rout_failure": process_rout_failure,
}


def process_context():
    context_events = load_json(CONTEXT_FILE)
    base_input_data = load_json(BASE_INPUT_FILE)

    processed_events = []

    for event in context_events:
        processor = EVENT_PROCESSORS.get(event.get("event_type"))
        if processor is None:
            processed_events.append(event)
            continue

        processed_events.append(processor(event, base_input_data))

    save_json(CONTEXT_FILE, processed_events)
    return processed_events


if __name__ == "__main__":
    events = process_context()
    processed_count = sum("SAT" in event for event in events)
    print(f"Processed {processed_count} event(s). Updated {CONTEXT_FILE}.")

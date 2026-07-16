import argparse
import copy
import json
from pathlib import Path


CONTEXT_MODEL_VERSION = 1
SUPPORTED_FAILURE_TYPES = ("processor_failure",)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


def end_system_ids(data):
    return sorted(
        node["id"]
        for node in data["platform"]["nodes"]
        if not node["is_router"]
    )


def resolve_processor_ids(data, requested_processor_ids):
    if not requested_processor_ids:
        return None

    processors = end_system_ids(data)
    processor_by_text = {str(processor_id): processor_id for processor_id in processors}
    resolved = []
    unknown = []

    for requested_id in requested_processor_ids:
        if requested_id in processor_by_text:
            resolved_id = processor_by_text[requested_id]
            if resolved_id not in resolved:
                resolved.append(resolved_id)
        else:
            unknown.append(requested_id)

    if unknown:
        available = ", ".join(str(processor_id) for processor_id in processors)
        missing = ", ".join(unknown)
        raise ValueError(
            f"Unknown failed processor id(s): {missing}. "
            f"Available processors are: {available}"
        )

    return resolved


def expand_job_can_run_on(data, mode, min_count):
    if mode == "none" and min_count is None:
        return []

    processors = end_system_ids(data)
    changes = []

    for job in data["application"]["jobs"]:
        original = list(job["can_run_on"])

        if mode == "all":
            expanded = list(processors)
        else:
            expanded = list(original)

        if min_count is not None and len(expanded) < min_count:
            for processor_id in processors:
                if processor_id not in expanded:
                    expanded.append(processor_id)
                if len(expanded) >= min_count:
                    break

        expanded = sorted(expanded)
        job["can_run_on"] = expanded

        if expanded != original:
            changes.append(
                {
                    "job_id": job["id"],
                    "original_can_run_on": original,
                    "expanded_can_run_on": expanded,
                }
            )

    return changes


def build_processor_failure_context(
    input_file,
    data,
    include_nominal,
    failed_processor_ids=None,
):
    scenarios = []

    if include_nominal:
        scenarios.append(
            {
                "id": "nominal",
                "type": "nominal",
                "failed_processors": [],
            }
        )

    processors_to_fail = (
        failed_processor_ids
        if failed_processor_ids is not None
        else end_system_ids(data)
    )

    for processor_id in processors_to_fail:
        scenarios.append(
            {
                "id": f"processor_{processor_id}_failed",
                "type": "processor_failure",
                "failed_processors": [processor_id],
            }
        )

    return {
        "version": CONTEXT_MODEL_VERSION,
        "base_input": input_file,
        "failure_types": list(SUPPORTED_FAILURE_TYPES),
        "scenarios": scenarios,
    }


def apply_processor_failure(base_data, scenario):
    failed_processors = set(scenario["failed_processors"])
    scenario_data = copy.deepcopy(base_data)

    scenario_data["platform"]["nodes"] = [
        node
        for node in scenario_data["platform"]["nodes"]
        if node["id"] not in failed_processors
    ]

    scenario_data["platform"]["links"] = [
        link
        for link in scenario_data["platform"].get("links", [])
        if (
            link["start"] not in failed_processors
            and link["end"] not in failed_processors
        )
    ]

    unavailable_jobs = []
    for job in scenario_data["application"]["jobs"]:
        job["can_run_on"] = [
            processor_id
            for processor_id in job["can_run_on"]
            if processor_id not in failed_processors
        ]
        if not job["can_run_on"]:
            unavailable_jobs.append(job["id"])

    scenario_data["context"] = {
        "scenario_id": scenario["id"],
        "type": scenario["type"],
        "failed_processors": scenario["failed_processors"],
    }

    return scenario_data, unavailable_jobs


def generate_scenario_inputs(input_file, context_model, base_data, output_dir, prefix):
    generated = []
    skipped = []

    for scenario in context_model["scenarios"]:
        if scenario["type"] == "nominal":
            scenario_data = copy.deepcopy(base_data)
            scenario_data["context"] = {
                "scenario_id": scenario["id"],
                "type": scenario["type"],
                "failed_processors": [],
            }
            unavailable_jobs = []
        elif scenario["type"] == "processor_failure":
            scenario_data, unavailable_jobs = apply_processor_failure(base_data, scenario)
        else:
            skipped.append(
                {
                    "scenario_id": scenario["id"],
                    "reason": f"Unsupported scenario type: {scenario['type']}",
                }
            )
            continue

        if unavailable_jobs:
            skipped.append(
                {
                    "scenario_id": scenario["id"],
                    "reason": "At least one job has no remaining processor.",
                    "jobs": unavailable_jobs,
                }
            )
            continue

        output_file = output_dir / f"{prefix}_{scenario['id']}.json"
        write_json(output_file, scenario_data)
        scenario["generated_input"] = str(output_file)
        generated.append(str(output_file))

    context_model["generated_inputs"] = generated
    context_model["skipped_scenarios"] = skipped

    return generated, skipped


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build a context model and derived scheduler inputs for multi-schedule graphs. "
            "Currently supports processor-failure scenarios only."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Base complete scheduler input JSON.",
    )
    parser.add_argument(
        "--context-output",
        help="Where to write the context model JSON.",
    )
    parser.add_argument(
        "--scenario-output-dir",
        default="input/context_scenarios",
        help="Directory for generated scenario input JSON files.",
    )
    parser.add_argument(
        "--prefix",
        help="Filename prefix for generated scenario inputs. Defaults to the base input name.",
    )
    parser.add_argument(
        "--include-nominal",
        action="store_true",
        help="Also generate the no-failure scenario.",
    )
    parser.add_argument(
        "--failed-processor",
        action="append",
        help=(
            "Generate a processor-failure scenario only for this processor id. "
            "Can be passed more than once. Default: generate one scenario for "
            "every processor."
        ),
    )
    parser.add_argument(
        "--expand-can-run-on",
        choices=("none", "all"),
        default="none",
        help=(
            "Optionally expand every job's can_run_on before generating failure "
            "scenarios. 'all' allows every job to run on every end system."
        ),
    )
    parser.add_argument(
        "--min-can-run-on",
        type=int,
        help=(
            "Ensure every job has at least this many can_run_on processors by "
            "adding processors from the platform before generating scenarios."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_file = args.input
    base_data = load_json(input_file)
    base_name = Path(input_file).stem
    prefix = args.prefix or base_name

    if args.min_can_run_on is not None and args.min_can_run_on < 1:
        raise ValueError("--min-can-run-on must be at least 1")

    failed_processor_ids = resolve_processor_ids(
        data=base_data,
        requested_processor_ids=args.failed_processor,
    )

    context_output = (
        Path(args.context_output)
        if args.context_output
        else Path("contexts") / f"{base_name}_processor_failures.json"
    )
    scenario_output_dir = Path(args.scenario_output_dir)

    context_model = build_processor_failure_context(
        input_file=input_file,
        data=base_data,
        include_nominal=args.include_nominal,
        failed_processor_ids=failed_processor_ids,
    )
    context_model["can_run_on_expansion"] = expand_job_can_run_on(
        data=base_data,
        mode=args.expand_can_run_on,
        min_count=args.min_can_run_on,
    )

    generated, skipped = generate_scenario_inputs(
        input_file=input_file,
        context_model=context_model,
        base_data=base_data,
        output_dir=scenario_output_dir,
        prefix=prefix,
    )
    write_json(context_output, context_model)

    print(f"Context model written to {context_output}")
    print(f"Generated {len(generated)} scenario input file(s).")
    if skipped:
        print(f"Skipped {len(skipped)} infeasible scenario(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

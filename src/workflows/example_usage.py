"""Example usage of workflow pipelines."""
from workflows.runner import WorkflowRunner


def example_data_load():
    """Example: Run DataLoad pipeline."""
    runner = WorkflowRunner()
    results = runner.run_data_load(count=10, batch_size=5)
    print(f"Processed {len(results)} posts")
    return results


def example_research():
    """Example: Run Research pipeline."""
    runner = WorkflowRunner()
    results = runner.run_research(count=1)
    print(f"Researched {len(results)} posts")
    return results


def example_gen_angles():
    """Example: Run GenAngles pipeline."""
    runner = WorkflowRunner()
    results = runner.run_gen_angles(count=1)
    print(f"Generated angles for {len(results)} posts")
    return results


def example_stego():
    """Example: Run Stego pipeline."""
    runner = WorkflowRunner()
    result = runner.run_stego(
        post_id="example_post_id",
        payload="secret message",
        tag="test",
    )
    print(f"Stego encoding: {result.get('succeeded')}")
    return result


def example_full_pipeline():
    """Example: Run full pipeline."""
    runner = WorkflowRunner()
    results = runner.run_full_pipeline(
        start_step="filter-url-unresolved",
        count=1,
    )
    print(f"Full pipeline processed {len(results)} posts")
    return results


if __name__ == "__main__":
    # Uncomment to run examples:
    # example_data_load()
    # example_research()
    # example_gen_angles()
    # example_stego()
    # example_full_pipeline()
    pass

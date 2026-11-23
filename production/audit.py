
def snapshot_production_run(run):
    """
    Return a JSON-safe snapshot of a production run
    and its raw-material consumption records.
    """

    return {
        "run_number": run.run_number,
        "product_recipe_id": run.product_recipe_id,
        "batch_count": run.batch_count,
        "production_date": (
            run.production_date.isoformat()
            if run.production_date else None
        ),
        "actual_good_output": str(run.actual_good_output),
        "status": run.status,
        "notes": run.notes,
        "consumptions": [
            {
                "raw_material_id": item.raw_material_id,
                "planned_quantity": str(item.planned_quantity),
                "actual_quantity": str(item.actual_quantity),
            }
            for item in run.consumptions.order_by("raw_material_id")
        ],
    }

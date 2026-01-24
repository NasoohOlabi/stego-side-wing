import json

from genson import SchemaBuilder


def generalize_search_results(schema_dict):
    """Recursively finds search_results and converts properties to additionalProperties."""
    if not isinstance(schema_dict, dict):
        return

    for key, value in list(schema_dict.items()):
        if key == "search_results" and "properties" in value:
            # Grab the schema of the first property found
            props = value["properties"]
            if props:
                first_val = list(props.values())[0]
                # Transform to Record type
                value["additionalProperties"] = first_val
                value.pop("properties", None)
                value.pop("required", None)

        # Keep searching deeper
        if isinstance(value, dict):
            generalize_search_results(value)
        elif isinstance(value, list):
            for item in value:
                generalize_search_results(item)


# --- Execution ---
input_path = "output-results/1look5n_v3.json"
output_path = "schema.json"

builder = SchemaBuilder()
with open(input_path, "r", encoding="utf-8") as f:
    builder.add_object(json.load(f))

schema = builder.to_schema()
generalize_search_results(schema)

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(schema, f, indent=2, ensure_ascii=False)

print("Done! Check schema.json for 'additionalProperties'.")

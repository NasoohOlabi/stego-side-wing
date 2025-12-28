// Loop over input items and add a new field called 'myNewField' to the JSON of each one
for (const item of $input.all()) {
	if (!!item.json.angles && !!item.json.data) {
		item.json = { ...item.json.data, angles: item.json.angles };
	}
}

return $input.all();

def compile_template(annotations, grid):
    sections = {}

    for a in annotations:
        sid = a["scope_id"]
        sections.setdefault(sid, {})
        sections[sid][a["tag"]] = a

    compiled = []

    for sid, items in sections.items():
        from_cell = items["FROM_KEYWORD"]
        until_cell = items["UNTIL_KEYWORD"]
        data_start = items["START_DATA"]
        data_end = items["END_DATA"]

        compiled.append({
            "section_name": sid,
            "data": {
                "from_keyword": from_cell["value"],
                "including_spaces1": "1",
                "skip_rows": data_start["row"] - from_cell["row"],
                "skip_cols": data_start["col"] - from_cell["col"],
                "until_keyword": until_cell["value"],
                "including_spaces2": "1",
                "extract_upto_rows": until_cell["row"] - data_end["row"],
                "extract_upto_cols": until_cell["col"] - data_end["col"]
            }
        })

    return {"sections": compiled}

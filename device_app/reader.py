def parse_line(raw):
    out = {}
    for pair in raw.strip().split(","):
        if "=" in pair:
            key, val = pair.split("=", 1)
            try:
                out[key] = float(val)
            except ValueError:
                pass          # ignore corrupt values
    return out
import pathlib
for line in pathlib.Path('secrets/marketing-db.env').read_text().splitlines():
    if line.startswith('MI_DATABASE_URL='):
        value = line.split('=', 1)[1].strip().strip('"').strip("'")
        if value:
            print(value, end='')
            break

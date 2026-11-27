"""Print the dedicated marketing database URL from the project secret file.

Used only inside prod_psql.sh via command substitution so the value is never
echoed to logs, reports or the terminal.
"""
import pathlib
for line in pathlib.Path('secrets/marketing-service.env').read_text().splitlines():
    if line.startswith('MI_DATABASE_URL='):
        value = line.split('=', 1)[1].strip().strip('"').strip("'")
        if value:
            print(value, end='')
            break

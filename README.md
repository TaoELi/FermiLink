# FermiLink (Rebuilt Layout)

This repository contains a new `src/fermilink` package layout aimed at clean packaging and long-term maintenance.

## Install

```bash
pip install .
```

## CLI

```bash
fermilink install ase --activate
fermilink dependencies maxwelllink --package meep
fermilink start
fermilink restart
fermilink stop
```

`fermilink install <package>` uses the default `TEL-Research-Group` channel unless `--zip-url` is supplied.

# TigerGraph Community Edition, local

## Start

```
docker compose -f infra/docker-compose.yml up -d
```

First boot takes a few minutes: the container has to initialise the cluster
before GSQL answers. Watch it with:

```
docker logs -f hhgoa-tigergraph
```

## Check it is up

```
curl -s http://localhost:14240/api/ping
```

GraphStudio is at http://localhost:14240. Default login is `tigergraph` /
`tigergraph`; change the password and put the new one in `.env` as
`TG_PASSWORD`.

## Where the CSVs are

`data/` is mounted read only at `/home/tigergraph/data` inside the container,
so GSQL loading jobs reference paths like
`/home/tigergraph/data/transactions.csv`. Nothing is copied, so the 708 MB
transaction file is not duplicated.

## Stop without losing the graph

```
docker compose -f infra/docker-compose.yml stop
```

`docker compose ... down -v` deletes the `tg_data` volume and the loaded graph
with it. Use `stop` unless you mean to start over.
